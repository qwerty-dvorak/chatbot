# ruff: noqa: D100, D101, D103
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from pipeline.config import cfg
from pipeline.extract import extract
from pipeline.jobs import TERMINAL_STATUSES, IngestionWorker, JobStore
from pipeline.milvus import connect_milvus, ensure_collection, get_client
from pipeline.models import IngestionTier
from pipeline.process import process_image, process_text
from pipeline.progress import ProgressTracker
from pipeline.query import enhance_query
from pipeline.search import search
from pipeline.tiers import options_for_tier, tier_from_str

logger = logging.getLogger(__name__)
job_store = JobStore(cfg.ingestion_data_dir)


def _execute_job(job: dict) -> dict:
    payload = job["payload"]
    if job["kind"] != "ingest":
        msg = "Unsupported job kind"
        raise ValueError(msg)

    tier = tier_from_str(payload.get("tier", IngestionTier.SLOW.value))
    options = options_for_tier(tier)
    upload_dir = Path(payload["path"])
    file_paths = sorted(path for path in upload_dir.iterdir() if path.is_file())
    results: list[dict] = []
    errors: list[dict[str, str]] = []

    def persist_steps(_job_id: str, steps: list[dict]) -> None:
        job_store.update_steps(_job_id, steps)

    for file_path in file_paths:
        source_name = file_path.name
        try:
            doc = extract(str(file_path), fast=tier == IngestionTier.INSTANT)
            text_steps = ["extract", "store_raw", "db_insert", "chunk", "summary", "hyde", "persist", "embed", "index"]
            image_steps = ["extract", "store_raw", "ocr", "db_insert", "persist", "embed", "index", "chunk", "hyde"]
            step_names = image_steps if doc.images else text_steps
            tracker = ProgressTracker(job["id"], step_names, persist_fn=persist_steps)
            tracker.start("extract", f"file={source_name}")
            tracker.complete("extract", f"type={doc.content_type.value}")

            existing_doc_id = payload.get("document_id")
            chunk_strategy = payload.get("strategy") or options.chunk_strategy
            generate_hyde = (
                payload["hypothetical_questions"]
                if payload.get("hypothetical_questions") is not None
                else options.hypothetical_questions_per_chunk > 0
            )
            if doc.images:
                use_multimodal = options.use_multimodal_embedding or (tier == IngestionTier.INSTANT and not doc.text.strip())
                result = process_image(
                    doc,
                    params={
                        "existing_document_id": existing_doc_id,
                        "embedding_model": cfg.multimodal_embedding_model,
                        "embedding_dim": cfg.multimodal_embedding_dim,
                        "ocr_mode": payload.get("ocr_mode") or ("none" if tier == IngestionTier.INSTANT else cfg.ocr_mode),
                        "use_multimodal_embedding": use_multimodal,
                        "use_text_embedding": options.use_text_embedding,
                        "chunk_strategy": chunk_strategy,
                        "generate_hyde": generate_hyde,
                        "progress_tracker": tracker,
                    },
                )
            else:
                result = process_text(
                    doc,
                    params={
                        "existing_document_id": existing_doc_id,
                        "chunk_strategy": chunk_strategy,
                        "generate_hyde": generate_hyde,
                        "hyde_per_chunk": options.hypothetical_questions_per_chunk,
                        "generate_summary": True,
                        "progress_tracker": tracker,
                    },
                )
            results.append(result)
            logger.info("[job %s] file=%s completed", job["id"][:8], source_name)
        except Exception as exc:
            logger.exception("Failed to process %s", source_name)
            errors.append({"file": source_name, "error": str(exc)})

    if errors and not results:
        msg = "All uploaded files failed ingestion"
        raise RuntimeError(msg)

    return {"tier": tier.value, "files_processed": len(results), "files_failed": len(errors), "results": results, "errors": errors}


ingestion_worker = IngestionWorker(job_store, handler=_execute_job, poll_interval=cfg.ingestion_poll_interval)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    job_store.initialize()
    ingestion_worker.start()
    try:
        connect_milvus()
    except Exception:  # noqa: BLE001
        logger.warning("Could not connect to Milvus on startup")
    yield
    ingestion_worker.stop()


app = FastAPI(title="RAG Pipeline API", description="Document ingestion and retrieval service.", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def timing_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.time()
    response = await call_next(request)
    logger.info("[TIMING] %.3fs %s %s %s", time.time() - start, request.method, request.url.path, response.status_code)
    return response


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    use_reranker: bool = True
    hierarchical: bool = True
    hyde: bool = True
    sub_queries: bool = True
    stepback: bool = True
    tier: IngestionTier | None = None
    enhancements: str | None = None
    artifact_sources: list[str] | None = None


class SearchResponse(BaseModel):
    query: str
    enhanced_queries: list[str]
    retrieval_mode: str
    use_reranker: bool
    hierarchical: bool
    results: list[dict]
    total: int
    timing: dict[str, float | str]


def _public_job(job: dict) -> dict:
    payload = job["payload"]
    request = {k: v for k, v in payload.items() if k != "path"}
    steps = job.get("steps") or []
    step_summary = {}
    if steps:
        running = next((s for s in steps if s["status"] == "running"), None)
        step_summary = {
            "total": len(steps),
            "completed": sum(1 for s in steps if s["status"] == "completed"),
            "failed": sum(1 for s in steps if s["status"] == "failed"),
            "current_step": running["name"] if running else None,
        }
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "files": job["filenames"],
        "request": request,
        "result": job["result"],
        "error": job["error"],
        "steps": steps,
        "step_summary": step_summary,
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "links": {"self": f"/v1/ingestions/{job['id']}", "collection": "/v1/ingestions"},
    }


def _get_job_or_404(job_id: str) -> dict:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Ingestion job {job_id!r} not found")
    return job


def _safe_upload_name(filename: str | None, used: set[str]) -> str:
    candidate = Path(filename or "upload").name
    if candidate in {"", ".", ".."}:
        candidate = "upload"
    original = candidate
    counter = 2
    while candidate in used:
        p = Path(original)
        candidate = f"{p.stem}-{counter}{p.suffix}"
        counter += 1
    used.add(candidate)
    return candidate


@app.get("/health/live")
async def liveness() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> dict:
    try:
        collections = get_client().list_collections()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Milvus unavailable") from exc
    return {"status": "ready", "worker_running": ingestion_worker.running, "queue": job_store.counts(), "collections": len(collections)}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "milvus_host": cfg.milvus_host,
        "milvus_port": cfg.milvus_port,
        "worker_running": ingestion_worker.running,
        "queue": job_store.counts(),
    }


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.post("/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_ingestion(  # noqa: PLR0913
    files: Annotated[list[UploadFile], File()],
    tier: Annotated[IngestionTier, Form()] = IngestionTier.SLOW,
    strategy: Annotated[Literal["recursive", "sentence_window", "hierarchical"] | None, Form()] = None,
    hypothetical_questions: Annotated[bool | None, Form()] = None,
    ocr_mode: Annotated[Literal["none", "basic", "paddleocr"] | None, Form()] = None,
    document_id: Annotated[str | None, Form()] = None,
) -> dict:
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")
    job_id = uuid.uuid4().hex
    upload_dir = job_store.upload_dir / job_id
    upload_dir.mkdir(parents=True, exist_ok=False)
    filenames: list[str] = []
    used_names: set[str] = set()
    try:
        for upload in files:
            filename = _safe_upload_name(upload.filename, used_names)
            destination = upload_dir / filename
            async with aiofiles.open(destination, "wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    await output.write(chunk)
            await upload.close()
            filenames.append(filename)
        job = job_store.create(
            kind="ingest",
            payload={
                "path": str(upload_dir),
                "tier": tier.value,
                "strategy": strategy,
                "hypothetical_questions": hypothetical_questions,
                "ocr_mode": ocr_mode,
                "document_id": document_id,
            },
            filenames=filenames,
            job_id=job_id,
        )
        ingestion_worker.notify()
        return _public_job(job)
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise


@app.get("/v1/ingestions")
async def list_ingestions(
    job_status: Annotated[Literal["queued", "running", "succeeded", "failed", "cancelled"] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    jobs = job_store.list(status=job_status, limit=limit)
    return {"jobs": [_public_job(job) for job in jobs], "total": len(jobs)}


@app.get("/v1/ingestions/{job_id}")
async def get_ingestion(job_id: str) -> dict:
    return _public_job(_get_job_or_404(job_id))


@app.delete("/v1/ingestions/{job_id}")
async def cancel_ingestion(job_id: str) -> dict:
    job = _get_job_or_404(job_id)
    if job["status"] in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is already {job['status']} and cannot be cancelled")
    if job["status"] == "running":
        raise HTTPException(status_code=409, detail="Running ingestion cannot be interrupted safely")
    job_store.cancel(job_id)
    return _public_job(_get_job_or_404(job_id))


def _search_options(body: SearchRequest) -> tuple[str | tuple[str, ...], bool]:
    use_reranker = body.use_reranker
    if body.tier is not None:
        tier_options = options_for_tier(body.tier)
        use_reranker = tier_options.use_reranker
    if body.enhancements is not None:
        return body.enhancements, use_reranker
    if body.tier is not None:
        return options_for_tier(body.tier).query_enhancements, use_reranker
    enabled = [k for k in ("hyde", "sub_queries", "stepback") if getattr(body, k)]
    return tuple(enabled), use_reranker


@app.post("/v1/search")
async def search_endpoint(body: SearchRequest) -> SearchResponse:
    try:
        enhancements, use_reranker = _search_options(body)
        enhanced_queries = enhance_query(body.query, enhancements=enhancements)
        results, timing = search(
            body.query,
            top_k=body.top_k,
            use_reranker=use_reranker,
            retrieval_mode=body.mode,
            enhancements=enhancements,
            hierarchical=body.hierarchical,
            artifact_sources=body.artifact_sources,
            pre_enhanced_queries=enhanced_queries,
        )
        formatted = [
            {
                "rank": i + 1,
                "score": r.score,
                "method": r.retrieval_method,
                "source": r.chunk.source_path,
                "chunk_type": r.chunk.chunk_type.value,
                "text": (r.chunk.window_text or r.chunk.text)[:1000],
                "has_image": r.chunk.image_data is not None,
                "metadata": r.chunk.metadata,
            }
            for i, r in enumerate(results)
        ]
        return SearchResponse(
            query=body.query,
            enhanced_queries=enhanced_queries,
            retrieval_mode=body.mode,
            use_reranker=use_reranker,
            hierarchical=body.hierarchical,
            results=formatted,
            total=len(formatted),
            timing=timing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/collections")
async def list_collections() -> dict:
    try:
        collections = get_client().list_collections()
        result = []
        for c in collections:
            name = c.get("name", "") if isinstance(c, dict) else str(c)
            result.append(
                {
                    "name": name,
                    "embedding_model": cfg.text_embedding_model,
                    "embedding_dim": cfg.text_embedding_dim,
                    "collection_name": cfg.text_collection if name == cfg.text_collection else name,
                }
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        return {"collections": result}


@app.get("/v1/collections/{name}/stats")
async def collection_stats(name: str) -> dict:
    try:
        ensure_collection(name, cfg.text_embedding_dim)
        client = get_client()
        count = (
            client.count(collection_name=name)
            if hasattr(client, "count")
            else len(list(client.query(collection_name=name, output_fields=["id"], limit=10000)))
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        return {
            "name": name,
            "embedding_model": cfg.text_embedding_model,
            "embedding_dim": cfg.text_embedding_dim,
            "milvus_host": cfg.milvus_host,
            "milvus_port": cfg.milvus_port,
            "count": count,
        }


@app.delete("/v1/collections/{name}")
async def drop_collection(name: str) -> dict:
    try:
        get_client().drop_collection(name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        return {"dropped": name}
