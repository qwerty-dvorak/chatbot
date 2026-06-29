import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from pipeline.config import cfg
from pipeline.extract import extract, extract_fast
from pipeline.index import _ensure_collection, connect_milvus, get_client
from pipeline.jobs import TERMINAL_STATUSES, IngestionWorker, JobStore
from pipeline.models import IngestionTier
from pipeline.progress import ProgressTracker
from pipeline.query import enhance_query
from pipeline.search import search
from pipeline.tiers import options_for_tier, tier_from_str

logger = logging.getLogger(__name__)

job_store = JobStore(cfg.ingestion_data_dir)


def _execute_job(job: dict) -> dict:
    payload = job["payload"]
    job_id = job["id"]

    if job["kind"] == "ingest":
        step_names = [
            "extract", "store_raw", "ocr", "db_insert", "chunk", "text_pipeline",
            "summary", "hyde", "persist", "embed", "index",
        ]
        tracker = ProgressTracker(
            job_id,
            step_names,
            persist_fn=lambda jid, steps: job_store.update_steps(jid, steps),
        )

        from pipeline.image_pipeline import process_document as image_process
        from pipeline.text_pipeline import process_document as text_process

        tier = tier_from_str(payload.get("tier", IngestionTier.SLOW.value))
        options = options_for_tier(tier)
        upload_dir = Path(payload["path"])
        file_paths = sorted(path for path in upload_dir.iterdir() if path.is_file())
        results: list[dict] = []
        errors: list[dict[str, str]] = []

        for file_path in file_paths:
            source_name = file_path.name
            try:
                tracker.start("extract", f"extracting {source_name}")
                doc = extract_fast(str(file_path)) if tier == IngestionTier.INSTANT else extract(str(file_path))
                tracker.complete("extract", f"{source_name} ({doc.content_type.value})")

                existing_doc_id = payload.get("document_id")
                chunk_strategy = payload.get("strategy") or options.chunk_strategy
                generate_hyde = (
                    payload["hypothetical_questions"]
                    if payload.get("hypothetical_questions") is not None
                    else options.hypothetical_questions_per_chunk > 0
                )

                if doc.images:
                    result = image_process(
                        doc,
                        params={
                            "existing_document_id": existing_doc_id,
                            "embedding_model": cfg.multimodal_embedding_model,
                            "embedding_dim": cfg.multimodal_embedding_dim,
                            "ocr_mode": payload.get("ocr_mode") or (
                                "none" if tier == IngestionTier.INSTANT else cfg.ocr_mode
                            ),
                            "use_multimodal_embedding": options.use_multimodal_embedding,
                            "use_text_embedding": options.use_text_embedding,
                            "chunk_strategy": chunk_strategy,
                            "generate_hyde": generate_hyde,
                        },
                        progress=tracker,
                    )
                else:
                    result = text_process(
                        doc,
                        params={
                            "existing_document_id": existing_doc_id,
                            "chunk_strategy": chunk_strategy,
                            "generate_hyde": generate_hyde,
                            "hyde_per_chunk": options.hypothetical_questions_per_chunk,
                            "generate_summary": True,
                        },
                        progress=tracker,
                    )
                results.append(result)
            except Exception as exc:
                logger.exception("Failed to process %s", source_name)
                if tracker.current_step:
                    tracker.fail(tracker.current_step, str(exc), f"{source_name} failed")
                errors.append({"file": source_name, "error": str(exc)})

        if errors and not results:
            messages = "; ".join(item["error"] for item in errors)
            raise RuntimeError(f"All uploaded files failed ingestion: {messages}")

        return {
            "tier": tier.value,
            "files_processed": len(results),
            "files_failed": len(errors),
            "results": results,
            "errors": errors,
        }

    raise ValueError(f"Unsupported job kind: {job['kind']}")


ingestion_worker = IngestionWorker(
    job_store,
    handler=_execute_job,
    poll_interval=cfg.ingestion_poll_interval,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_store.initialize()
    ingestion_worker.start()
    try:
        connect_milvus()
    except Exception as exc:
        print(f"[api] WARNING: Could not connect to Milvus on startup: {exc}")
        print("[api] Service will start; readiness reports the connection failure.")
    yield
    ingestion_worker.stop()


app = FastAPI(
    title="RAG Pipeline API",
    description=(
        "Local document ingestion and retrieval service. Ingestion is durable, "
        "queued, and processed by a single background worker. Documents are "
        "ingested at instant or slow tier."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def timing_middleware(request, call_next):
    import time

    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(
        "[TIMING] %.3fs %s %s %s",
        duration,
        request.method,
        request.url.path,
        response.status_code,
    )
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
    request = {key: value for key, value in payload.items() if key != "path"}
    steps = job.get("steps") or []
    step_summary = {}
    if steps:
        running = next((step for step in steps if step["status"] == "running"), None)
        step_summary = {
            "total": len(steps),
            "completed": sum(1 for step in steps if step["status"] == "completed"),
            "failed": sum(1 for step in steps if step["status"] == "failed"),
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
        "links": {
            "self": f"/v1/ingestions/{job['id']}",
            "collection": "/v1/ingestions",
        },
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
        path = Path(original)
        candidate = f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    used.add(candidate)
    return candidate


@app.get("/health/live")
async def liveness():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    try:
        collections = get_client().list_collections()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Milvus unavailable: {exc}") from exc
    return {
        "status": "ready",
        "worker_running": ingestion_worker.running,
        "queue": job_store.counts(),
        "collections": len(collections),
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "milvus_host": cfg.milvus_host,
        "milvus_port": cfg.milvus_port,
        "worker_running": ingestion_worker.running,
        "queue": job_store.counts(),
    }


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.post("/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_ingestion(
    files: list[UploadFile] = File(...),
    tier: IngestionTier = Form(IngestionTier.SLOW),
    strategy: Literal["recursive", "sentence_window", "hierarchical"] | None = Form(None),
    hypothetical_questions: bool | None = Form(None),
    ocr_mode: Literal["none", "basic", "paddleocr"] | None = Form(None),
    document_id: str | None = Form(None),
):
    """Persist uploaded files and enqueue a long-running ingestion job."""
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
    job_status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=200),
):
    jobs = job_store.list(status=job_status, limit=limit)
    return {"jobs": [_public_job(job) for job in jobs], "total": len(jobs)}


@app.get("/v1/ingestions/{job_id}")
async def get_ingestion(job_id: str):
    return _public_job(_get_job_or_404(job_id))


@app.delete("/v1/ingestions/{job_id}")
async def cancel_ingestion(job_id: str):
    job = _get_job_or_404(job_id)
    if job["status"] in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job['status']} and cannot be cancelled",
        )
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
        enhancements: str | tuple[str, ...] = body.enhancements
    elif body.tier is not None:
        enhancements = options_for_tier(body.tier).query_enhancements
    else:
        enabled: list[str] = []
        if body.hyde:
            enabled.append("hyde")
        if body.sub_queries:
            enabled.append("sub_queries")
        if body.stepback:
            enabled.append("stepback")
        enhancements = tuple(enabled)

    return enhancements, use_reranker


@app.post("/v1/search", response_model=SearchResponse)
async def search_endpoint(body: SearchRequest):
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/collections")
async def list_collections():
    try:
        collections = get_client().list_collections()
        result = []
        for collection in collections:
            name = collection.get("name", "") if isinstance(collection, dict) else str(collection)
            result.append({
                "name": name,
                "embedding_model": cfg.text_embedding_model,
                "embedding_dim": cfg.text_embedding_dim,
                "collection_name": cfg.text_collection if name == cfg.text_collection else name,
            })
        return {"collections": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/collections/{name}/stats")
async def collection_stats(name: str):
    try:
        _ensure_collection(name, cfg.text_embedding_dim)
        client = get_client()
        if hasattr(client, "count"):
            count = client.count(collection_name=name)
        else:
            count = len(list(client.query(
                collection_name=name,
                output_fields=["id"],
                limit=10000,
            )))
        return {
            "name": name,
            "embedding_model": cfg.text_embedding_model,
            "embedding_dim": cfg.text_embedding_dim,
            "milvus_host": cfg.milvus_host,
            "milvus_port": cfg.milvus_port,
            "count": count,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/v1/collections/{name}")
async def drop_collection(name: str):
    try:
        get_client().drop_collection(name)
        return {"dropped": name}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
