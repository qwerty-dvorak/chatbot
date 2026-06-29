"""Milvus client helpers for vector collection management."""

import json

from pymilvus import DataType, MilvusClient

from .config import cfg
from .models import Chunk, ChunkType, EmbeddedChunk

_client: MilvusClient | None = None


def get_client() -> MilvusClient:
    """Get or create the global Milvus client."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = MilvusClient(uri=f"http://{cfg.milvus_host}:{cfg.milvus_port}")
    return _client


def connect_milvus() -> None:
    """Ensure the Milvus client is connected."""
    get_client()


def ensure_collection(name: str, dim: int) -> None:
    """Create or load a Milvus collection with the given name and dimension."""
    client = get_client()
    params = client.prepare_index_params()
    params.add_index(field_name="embedding", index_type="IVF_FLAT", metric_type="IP", params={"nlist": 128})

    if client.has_collection(name):
        if "embedding" not in client.list_indexes(name):
            client.create_index(collection_name=name, index_params=params)
        client.load_collection(name)
        return

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, max_length=64, is_primary=True)
    schema.add_field("source_path", DataType.VARCHAR, max_length=512)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
    schema.add_field("parent_id", DataType.VARCHAR, max_length=64)
    schema.add_field("window_text", DataType.VARCHAR, max_length=65535)
    schema.add_field("metadata_json", DataType.VARCHAR, max_length=4096)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    client.create_collection(collection_name=name, schema=schema)
    client.create_index(collection_name=name, index_params=params)
    client.load_collection(name)


def index_chunks(embedded: list[EmbeddedChunk]) -> None:
    """Insert embedded chunks into the appropriate Milvus collections."""
    text = [ec for ec in embedded if not ec.is_multimodal]
    images = [ec for ec in embedded if ec.is_multimodal]

    def _insert(items: list[EmbeddedChunk], collection: str, dim: int) -> None:
        client = get_client()
        ensure_collection(collection, dim)
        for start in range(0, len(items), 100):
            batch = items[start : start + 100]
            rows = []
            for ec in batch:
                c = ec.chunk
                rows.append(
                    {
                        "id": c.id,
                        "source_path": c.source_path,
                        "text": c.text[:65535],
                        "chunk_type": c.chunk_type.value if hasattr(c.chunk_type, "value") else str(c.chunk_type),
                        "parent_id": c.parent_id or "",
                        "window_text": (c.window_text or "")[:65535],
                        "metadata_json": json.dumps(c.metadata)[:4096],
                        "embedding": ec.embedding,
                    }
                )
            client.insert(collection_name=collection, data=rows)

    if text:
        _insert(text, cfg.text_collection, cfg.text_embedding_dim)
    if images:
        _insert(images, cfg.image_collection, cfg.multimodal_embedding_dim)


def chunk_from_hit(hit: dict) -> Chunk:
    """Reconstruct a Chunk from a Milvus search hit dict."""
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
