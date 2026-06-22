import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_milvus_available = None


def _check_milvus():
    global _milvus_available
    if _milvus_available is None:
        try:
            import pymilvus  # noqa: F401
            _milvus_available = True
        except ImportError:
            _milvus_available = False
            logger.warning("pymilvus is not installed; vector ops are no-ops")
    return _milvus_available


def get_milvus_client():
    from pymilvus import MilvusClient
    uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
    return MilvusClient(uri=uri)


def ensure_collection(collection_name, dimension, description=""):
    if not _check_milvus():
        return None
    client = get_milvus_client()
    if client.has_collection(collection_name):
        return client

    from pymilvus.milvus_client import IndexParams
    index_params = IndexParams()
    index_params.add_index(
        field_name="vector",
        index_type="IVF_FLAT",
        metric_type="IP",
        params={"nlist": 128},
    )
    client.create_collection(
        collection_name=collection_name,
        dimension=dimension,
        primary_field_name="id",
        id_type="string",
        max_length=36,
        description=description,
        metric_type="IP",
        index_params=index_params,
    )
    client.load_collection(collection_name)
    logger.info(f"Created Milvus collection: {collection_name} (dim={dimension})")
    return client


def ensure_collections():
    ensure_collection(
        settings.MILVUS_COLLECTION_CHUNKS,
        settings.TEXT_EMBEDDING_DIM,
        description="Document chunk embeddings (text)",
    )
    ensure_collection(
        settings.MILVUS_COLLECTION_MEMORIES,
        settings.TEXT_EMBEDDING_DIM,
        description="User memory embeddings",
    )


def insert_vectors(collection_name, vectors, metadata_list):
    if not _check_milvus():
        return []
    if not vectors:
        return []
    client = get_milvus_client()
    data = []
    for i, (vec, meta) in enumerate(zip(vectors, metadata_list)):
        data.append({
            "id": meta.get("id", str(i)),
            "vector": vec,
            **meta,
        })
    return client.insert(collection_name=collection_name, data=data)


def search_vectors(collection_name, query_vector, top_k=10, offset=0, expr=None):
    if not _check_milvus():
        return []
    client = get_milvus_client()
    kwargs = dict(
        collection_name=collection_name,
        data=[query_vector],
        limit=top_k,
        offset=offset,
        output_fields=["*"],
        search_params={
            "metric_type": "IP",
            "params": {"nprobe": 16},
        },
    )
    if expr:
        kwargs["filter"] = expr
    results = client.search(**kwargs)
    return results[0] if results else []


def delete_vectors(collection_name, ids):
    if not _check_milvus():
        return
    if not ids:
        return
    client = get_milvus_client()
    client.delete(collection_name=collection_name, ids=ids)


def delete_by_expr(collection_name, expr):
    if not _check_milvus():
        return
    client = get_milvus_client()
    client.delete(collection_name=collection_name, filter=expr)


def drop_collection(collection_name):
    if not _check_milvus():
        return
    client = get_milvus_client()
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)
