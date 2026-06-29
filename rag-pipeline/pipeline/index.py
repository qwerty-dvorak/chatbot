import json
import os
import pickle
import tempfile

from pymilvus import MilvusClient, DataType
from rank_bm25 import BM25Okapi

from .config import cfg
from .models import Chunk, ChunkType, EmbeddedChunk


# Module-level singleton — created once per process on first use.
_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    global _client
    if _client is None:
        _client = MilvusClient(uri=f"http://{cfg.milvus_host}:{cfg.milvus_port}")
    return _client


def connect_milvus() -> None:
    """Initialise the MilvusClient singleton (idempotent)."""
    get_client()


def _ensure_collection(name: str, dim: int) -> None:
    """Create collection + IVF_FLAT/IP index if it does not already exist, then load it.

    Schema:
      id             VARCHAR(64)    primary key
      source_path    VARCHAR(512)
      text           VARCHAR(65535)
      chunk_type     VARCHAR(32)
      parent_id      VARCHAR(64)
      window_text    VARCHAR(65535)
      metadata_json  VARCHAR(4096)
      embedding      FLOAT_VECTOR(dim)
    """
    client = get_client()
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="IVF_FLAT",
        metric_type="IP",
        params={"nlist": 128},
    )

    if client.has_collection(name):
        if "embedding" not in client.list_indexes(name):
            client.create_index(collection_name=name, index_params=index_params)
        client.load_collection(name)
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id",            DataType.VARCHAR,      max_length=64,    is_primary=True)
    schema.add_field("source_path",   DataType.VARCHAR,      max_length=512)
    schema.add_field("text",          DataType.VARCHAR,      max_length=65535)
    schema.add_field("chunk_type",    DataType.VARCHAR,      max_length=32)
    schema.add_field("parent_id",     DataType.VARCHAR,      max_length=64)
    schema.add_field("window_text",   DataType.VARCHAR,      max_length=65535)
    schema.add_field("metadata_json", DataType.VARCHAR,      max_length=4096)
    schema.add_field("embedding",     DataType.FLOAT_VECTOR, dim=dim)

    client.create_collection(collection_name=name, schema=schema)
    client.create_index(collection_name=name, index_params=index_params)
    client.load_collection(name)


def index_chunks(embedded: list[EmbeddedChunk]) -> None:
    """Insert embedded chunks into Milvus.

    Text chunks (is_multimodal=False) → cfg.text_collection
    Image chunks (is_multimodal=True) → cfg.image_collection
    Inserts in batches of 100.
    """
    text_chunks: list[EmbeddedChunk] = []
    image_chunks: list[EmbeddedChunk] = []
    for ec in embedded:
        (image_chunks if ec.is_multimodal else text_chunks).append(ec)

    def _insert_batch(items: list[EmbeddedChunk], collection_name: str) -> None:
        client = get_client()
        batch_size = 100
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            rows = []
            for ec in batch:
                c = ec.chunk
                rows.append({
                    "id":            c.id,
                    "source_path":   c.source_path,
                    "text":          c.text[:65535],
                    "chunk_type":    c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type),
                    "parent_id":     c.parent_id or "",
                    "window_text":   (c.window_text or "")[:65535],
                    "metadata_json": json.dumps(c.metadata)[:4096],
                    "embedding":     ec.embedding,
                })
            client.insert(collection_name=collection_name, data=rows)

    if text_chunks:
        _ensure_collection(cfg.text_collection, cfg.text_embedding_dim)
        _insert_batch(text_chunks, cfg.text_collection)

    if image_chunks:
        _ensure_collection(cfg.image_collection, cfg.multimodal_embedding_dim)
        _insert_batch(image_chunks, cfg.image_collection)


def _chunk_from_hit(hit: dict) -> Chunk:
    """Reconstruct a Chunk from a MilvusClient search or query result dict."""
    metadata_raw = hit.get("metadata_json", "{}")
    try:
        metadata = json.loads(metadata_raw)
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    chunk_type_raw = hit.get("chunk_type", "text")
    try:
        chunk_type = ChunkType(chunk_type_raw)
    except ValueError:
        chunk_type = ChunkType.TEXT

    return Chunk(
        id=hit.get("id", ""),
        source_path=hit.get("source_path", ""),
        text=hit.get("text", ""),
        chunk_type=chunk_type,
        metadata=metadata,
        parent_id=hit.get("parent_id") or None,
        window_text=hit.get("window_text") or None,
        children_ids=[],
        image_data=None,
    )


def build_bm25_index(chunks: list[Chunk]) -> BM25Okapi:
    """Build BM25 index from chunk texts and persist it."""
    tokenized_corpus = [chunk.text.lower().split() for chunk in chunks]
    bm25 = BM25Okapi(tokenized_corpus)
    save_bm25_index(bm25, chunks)
    return bm25


def load_bm25_index() -> tuple[BM25Okapi, list[Chunk]]:
    """Load BM25 index from cfg.bm25_index_path. Raises FileNotFoundError if absent."""
    path = cfg.bm25_index_path
    if not os.path.exists(path):
        raise FileNotFoundError(f"BM25 index not found at {path!r}")
    with open(path, "rb") as f:
        data = pickle.load(f)
    return data["bm25"], data["chunks"]


def save_bm25_index(bm25: BM25Okapi, chunks: list[Chunk]) -> None:
    """Persist BM25 index atomically so concurrent readers never see a partial file."""
    path = cfg.bm25_index_path
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".bm25-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
