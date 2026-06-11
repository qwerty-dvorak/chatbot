"""PostgreSQL knowledge store — writes into the *chatbot-service* database.

Connects to the same PostgreSQL database that the chatbot-service Django app
uses (same env vars, same tables).  All schema is managed by Django migrations
in ``chatbot-service/apps/knowledge/``; this module only inserts data.

Tables (owned by Django):
  ``knowledge_sources``  — source of truth for a group of documents
  ``documents``          — one row per ingested file
  ``document_chunks``    — chunked text with metadata (embedding params, HyDE)
  ``document_assets``    — image/page assets extracted from documents
"""

import json
import logging
import uuid
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

from .config import cfg

logger = logging.getLogger(__name__)

_connection_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


# ---------------------------------------------------------------------------
# Connection management (uses chatbot-service env vars)
# ---------------------------------------------------------------------------

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=cfg.postgres_pool_size,
            host=cfg.postgres_host,
            port=cfg.postgres_port,
            dbname=cfg.postgres_db,
            user=cfg.postgres_user,
            password=cfg.postgres_password,
        )
    return _connection_pool


def connect() -> None:
    _get_pool()


def execute(sql: str, params: tuple = (), fetch: bool = False):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            conn.commit()
            if fetch:
                return cur.fetchall()
            return None
    finally:
        pool.putconn(conn)


def execute_many(sql: str, params_list: list[tuple]) -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, params_list)
            conn.commit()
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# Default knowledge source (created once, reused for pipeline ingests)
# ---------------------------------------------------------------------------

_PIPELINE_SOURCE_ID: Optional[str] = None


def _ensure_pipeline_source() -> str:
    """Get-or-create a KnowledgeSource row for pipeline-ingested documents."""
    global _PIPELINE_SOURCE_ID
    if _PIPELINE_SOURCE_ID:
        rows = execute(
            "SELECT id FROM knowledge_sources WHERE id = %s LIMIT 1",
            (_PIPELINE_SOURCE_ID,), fetch=True,
        )
        if rows:
            return _PIPELINE_SOURCE_ID

    rows = execute(
        "SELECT id FROM knowledge_sources WHERE source_type = 'api' AND name = 'rag-pipeline' LIMIT 1",
        fetch=True,
    )
    if rows:
        _PIPELINE_SOURCE_ID = str(rows[0]["id"])
        return _PIPELINE_SOURCE_ID

    source_id = str(uuid.uuid4())
    execute(
        """INSERT INTO knowledge_sources
           (id, name, source_type, visibility, metadata, created_at, updated_at)
           VALUES (%s, 'rag-pipeline', 'api', 'private', '{}', NOW(), NOW())""",
        (source_id,),
    )
    _PIPELINE_SOURCE_ID = source_id
    return source_id


def reset_pipeline_source() -> None:
    """Clear cached pipeline source ID so _ensure_pipeline_source re-searches.
    Only needed in tests where the DB is cleaned between runs.
    """
    global _PIPELINE_SOURCE_ID
    _PIPELINE_SOURCE_ID = None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def insert_document(
    title: str,
    mime_type: str,
    original_filename: str = "",
    sha256: str = "",
    extracted_text: str = "",
    analysis_summary: str = "",
    metadata: dict | None = None,
) -> str:
    """Insert a Document row linked to the pipeline source.

    Returns the new document UUID.
    """
    doc_id = str(uuid.uuid4())
    source_id = _ensure_pipeline_source()
    execute(
        """INSERT INTO documents
           (id, source_id, title, original_filename, mime_type, sha256, status,
            extracted_text, analysis_summary, metadata, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, 'ready', %s, %s, %s, NOW(), NOW())""",
        (
            doc_id, source_id, title, original_filename or title,
            mime_type, sha256, extracted_text, analysis_summary,
            json.dumps(metadata or {}),
        ),
    )
    return doc_id


def get_document(doc_id: str) -> dict | None:
    rows = execute(
        "SELECT * FROM documents WHERE id = %s", (doc_id,), fetch=True,
    )
    return dict(rows[0]) if rows else None


def update_document_status(doc_id: str, status: str) -> None:
    execute("UPDATE documents SET status = %s WHERE id = %s", (status, doc_id))


# ---------------------------------------------------------------------------
# Document chunks
# ---------------------------------------------------------------------------

def insert_chunk(
    document_id: str,
    chunk_index: int,
    content: str,
    content_hash: str = "",
    token_count: int = 0,
    metadata: dict | None = None,
    asset_id: str | None = None,
) -> str:
    chunk_id = str(uuid.uuid4())
    execute(
        """INSERT INTO document_chunks
           (id, document_id, asset_id, chunk_index, content, content_hash,
            token_count, metadata, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        (
            chunk_id, document_id, asset_id, chunk_index, content,
            content_hash or _simple_hash(content),
            token_count, json.dumps(metadata or {}),
        ),
    )
    return chunk_id


def insert_chunks_batch(rows: list[dict]) -> list[str]:
    """Insert many chunk rows, returning their IDs in order."""
    ids = [str(uuid.uuid4()) for _ in rows]
    param_rows = []
    for chunk_id, r in zip(ids, rows):
        param_rows.append((
            chunk_id,
            r["document_id"],
            r.get("asset_id"),
            r["chunk_index"],
            r["content"],
            r.get("content_hash", _simple_hash(r["content"])),
            r.get("token_count", 0),
            json.dumps(r.get("metadata", {})),
        ))
    execute_many(
        """INSERT INTO document_chunks
           (id, document_id, asset_id, chunk_index, content, content_hash,
            token_count, metadata, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
        param_rows,
    )
    return ids


def get_chunks_by_document(document_id: str) -> list[dict]:
    rows = execute(
        "SELECT * FROM document_chunks WHERE document_id = %s ORDER BY chunk_index",
        (document_id,), fetch=True,
    )
    return [dict(r) for r in rows]


def get_chunk(chunk_id: str) -> dict | None:
    rows = execute(
        "SELECT * FROM document_chunks WHERE id = %s", (chunk_id,), fetch=True,
    )
    return dict(rows[0]) if rows else None


# ---------------------------------------------------------------------------
# Document assets (images, pages)
# ---------------------------------------------------------------------------

def insert_asset(
    document_id: str,
    asset_type: str,
    file: str = "",
    mime_type: str = "",
    page_number: int | None = None,
    text: str = "",
    metadata: dict | None = None,
) -> str:
    asset_id = str(uuid.uuid4())
    execute(
        """INSERT INTO document_assets
           (id, document_id, asset_type, file, mime_type, page_number, text, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            asset_id, document_id, asset_type, file, mime_type,
            page_number, text, json.dumps(metadata or {}),
        ),
    )
    return asset_id


# ---------------------------------------------------------------------------
# Knowledge-base stats
# ---------------------------------------------------------------------------

def knowledge_stats() -> dict:
    rows = execute(
        """SELECT
               (SELECT COUNT(*) FROM documents) AS documents,
               (SELECT COUNT(*) FROM document_chunks) AS chunks,
               (SELECT COUNT(*) FROM document_assets) AS assets
        """,
        fetch=True,
    )
    return dict(rows[0]) if rows else {
        "documents": 0, "chunks": 0, "assets": 0,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _simple_hash(text: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, text).hex
