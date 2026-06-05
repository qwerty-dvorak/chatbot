import os
import io
import uuid
import tempfile
import shutil
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException, Query as QueryParam
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import aiofiles

from pipeline.config import cfg
from pipeline.ingest import ingest_path
from pipeline.search import search, format_results
from pipeline.index import connect_milvus, get_client


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        connect_milvus()
    except Exception as exc:
        print(f"[api] WARNING: Could not connect to Milvus on startup: {exc}")
        print("[api] Service will start anyway; each pipeline call also connects.")
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RAG Pipeline API",
    description="Document ingestion and retrieval service",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    mode: str = "hybrid"          # hybrid | vector | bm25
    use_reranker: bool = True
    enhancements: str | None = None  # comma-sep overrides QUERY_ENHANCEMENTS


class SearchResponse(BaseModel):
    query: str
    results: list[dict]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Return service health and Milvus connection info."""
    return {"status": "ok", "milvus_host": cfg.milvus_host, "milvus_port": cfg.milvus_port}


@app.get("/")
async def root():
    """Redirect to interactive API docs."""
    return RedirectResponse(url="/docs")


@app.post("/v1/ingest")
async def ingest(
    files: list[UploadFile] = File(...),
    strategy: str = QueryParam("recursive"),
    hypothetical_questions: bool = QueryParam(False),
):
    """Upload and ingest one or more files into the RAG index."""
    tmpdir = tempfile.mkdtemp()
    try:
        filenames = []
        for upload in files:
            filename = upload.filename or f"upload_{uuid.uuid4().hex}"
            dest = Path(tmpdir) / filename
            # Use aiofiles for async write
            content = await upload.read()
            async with aiofiles.open(dest, "wb") as f:
                await f.write(content)
            filenames.append(filename)

        stats = ingest_path(
            tmpdir,
            strategy=strategy,
            add_hypothetical_questions=hypothetical_questions,
        )
        return {"status": "ok", "files": filenames, "stats": stats}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.post("/v1/search", response_model=SearchResponse)
async def search_endpoint(body: SearchRequest):
    """Search the RAG index with optional query enhancements."""
    try:
        # Temporarily override query enhancements if the caller specified them.
        original_enhancements = cfg.query_enhancements
        if body.enhancements is not None:
            cfg.query_enhancements = body.enhancements

        try:
            results = search(
                body.query,
                top_k=body.top_k,
                use_reranker=body.use_reranker,
                retrieval_mode=body.mode,
            )
        finally:
            # Always restore, even if search() raises.
            if body.enhancements is not None:
                cfg.query_enhancements = original_enhancements

        formatted = [
            {
                "rank": result.rank + 1,
                "score": result.score,
                "method": result.retrieval_method,
                "source": result.chunk.source_path,
                "chunk_type": result.chunk.chunk_type.value,
                "text": (result.chunk.window_text or result.chunk.text)[:1000],
                "has_image": result.chunk.image_data is not None,
                "metadata": result.chunk.metadata,
            }
            for result in results
        ]

        return SearchResponse(query=body.query, results=formatted, total=len(formatted))

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/v1/collections")
async def list_collections():
    """List all Milvus collections."""
    try:
        return get_client().list_collections()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/v1/collections/{name}")
async def drop_collection(name: str):
    """Drop a Milvus collection by name."""
    try:
        get_client().drop_collection(name)
        return {"dropped": name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
