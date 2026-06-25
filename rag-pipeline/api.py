<<<<<<< Updated upstream
import logging
=======
import os
import uuid
import tempfile
>>>>>>> Stashed changes
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from pipeline.config import cfg
<<<<<<< Updated upstream
from pipeline.index import connect_milvus, get_client, _ensure_collection
from pipeline.jobs import IngestionWorker, JobStore, TERMINAL_STATUSES
from pipeline.models import IngestionTier
from pipeline.progress import ProgressTracker
from pipeline.search import search
from pipeline.tiers import (
    options_for_tier,
    promote_document,
    tier_from_str,
=======
from pipeline.ingest import ingest_path
from pipeline.search import search, format_results
from pipeline.index import connect_milvus, get_client
from pipeline.models import IngestionTier
from pipeline.tiers import (
    ingest_tier,
    promote_document,
    tier_from_str,
    options_for_tier,
>>>>>>> Stashed changes
)


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
            job_id, step_names,
            persist_fn=lambda jid, steps: job_store.update_steps(jid, steps),
        )
        tracker.start("extract", "reading files from disk")

        # Use the new text/image pipelines with progress tracking
        from pipeline.text_pipeline import process_document as text_process
        from pipeline.image_pipeline import process_document as image_process
        from pipeline.extract import extract as do_extract

        upload_dir = Path(payload["path"])
        file_paths = sorted(
            p for p in upload_dir.iterdir() if p.is_file()
        )
        results = []
        errors = []

        for file_path in file_paths:
            source_name = file_path.name
            try:
                tracker.start("extract", f"extracting {source_name}")
                doc = do_extract(str(file_path))
                tracker.complete("extract", f"{source_name} ({doc.content_type.value})")

                existing_doc_id = payload.get("document_id")
                if doc.images:
                    tier = tier_from_str(payload.get("tier", IngestionTier.SLOW.value))
                    options = options_for_tier(tier)
                    result = image_process(doc, params={
                        "existing_document_id": existing_doc_id,
                        "embedding_model": cfg.multimodal_embedding_model,
                        "embedding_dim": cfg.multimodal_embedding_dim,
                        "ocr_mode": payload.get("ocr_mode") or (
                            "none" if tier == IngestionTier.INSTANT else cfg.ocr_mode
                        ),
                        "use_multimodal_embedding": options.use_multimodal_embedding,
                        "use_text_embedding": options.use_text_embedding,
                        "chunk_strategy": payload.get("strategy") or options.chunk_strategy,
                        "generate_hyde": payload.get("hypothetical_questions")
                        if payload.get("hypothetical_questions") is not None
                        else options.hypothetical_questions_per_chunk > 0,
                    }, progress=tracker)
                else:
                    # Text document -> use text pipeline
                    result = text_process(doc, params={
                        "existing_document_id": existing_doc_id,
                        "chunk_strategy": payload.get("strategy") or cfg.chunk_strategy,
                        "generate_hyde": payload.get("hypothetical_questions", True),
                    }, progress=tracker)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to process %s: %s", source_name, exc)
                current_step = tracker.current_step
                if current_step:
                    tracker.fail(current_step, str(exc), f"{source_name} failed")
                errors.append({"file": source_name, "error": str(exc)})

        combined = {
            "files_processed": len(results),
            "files_failed": len(errors),
            "results": results,
            "errors": errors,
        }

        if errors and not results:
            messages = "; ".join(item["error"] for item in errors)
            raise RuntimeError(f"All uploaded files failed ingestion: {messages}")
        return combined

    if job["kind"] == "promote":
        return promote_document(
            payload["source_path"],
            to_tier=tier_from_str(payload["to_tier"]),
            delete_old_chunks=payload["delete_old_chunks"],
        )
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
<<<<<<< Updated upstream
        "Local document ingestion and retrieval service. Ingestion is durable, "
        "queued, and processed by a single background worker."
=======
        "Document ingestion and retrieval service with three processing tiers.\n\n"
        "**Tiers:**\n"
        "- `instant` — text-only, no OCR, < 2 s/page.  For chat-session uploads.\n"
        "- `slow` — full OCR + multimodal embedding for one file.\n"
        "- `global` — batch mode, hierarchical chunks, all query enhancements.\n\n"
        "Documents can be promoted between tiers via `POST /v1/promote`."
>>>>>>> Stashed changes
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
    logger.info("[TIMING] %.3fs %s %s %s", duration, request.method, request.url.path, response.status_code)
    return response


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=100)
    mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    use_reranker: bool = True
<<<<<<< Updated upstream
    hierarchical: bool = True
    hyde: bool = True
    sub_queries: bool = True
    stepback: bool = True
    tier: IngestionTier | None = None
    enhancements: str | None = None
    artifact_sources: list[str] | None = None
=======
    enhancements: str | None = None   # comma-sep override for query_enhancements
    tier: str | None = None           # instant | slow | global — sets search defaults
>>>>>>> Stashed changes


class SearchResponse(BaseModel):
    query: str
    enhanced_queries: list[str]
    retrieval_mode: str
    use_reranker: bool
    hierarchical: bool
    results: list[dict]
    total: int
    timing: dict[str, float | str]


<<<<<<< Updated upstream
class PromotionRequest(BaseModel):
    source_path: str = Field(min_length=1)
    to_tier: IngestionTier
    delete_old_chunks: bool = True


def _public_job(job: dict) -> dict:
    payload = job["payload"]
    request = {key: value for key, value in payload.items() if key != "path"}
    steps = job.get("steps") or []
    step_summary = {}
    if steps:
        total = len(steps)
        completed = sum(1 for s in steps if s["status"] == "completed")
        failed = sum(1 for s in steps if s["status"] == "failed")
        running = next((s for s in steps if s["status"] == "running"), None)
        step_summary = {
            "total": total,
            "completed": completed,
            "failed": failed,
            "current_step": running["name"] if running else None,
        }
    response = {
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
    return response


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
        raise HTTPException(status_code=503, detail=f"Milvus unavailable: {exc}")
    return {
        "status": "ready",
        "worker_running": ingestion_worker.running,
        "queue": job_store.counts(),
        "collections": len(collections),
    }


@app.get("/health")
async def health():
=======
class PromoteRequest(BaseModel):
    source_path: str
    to_tier: str                  # instant | slow | global
    delete_old_chunks: bool = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Return service health and Milvus connection info."""
>>>>>>> Stashed changes
    return {
        "status": "ok",
        "milvus_host": cfg.milvus_host,
        "milvus_port": cfg.milvus_port,
<<<<<<< Updated upstream
        "worker_running": ingestion_worker.running,
        "queue": job_store.counts(),
=======
>>>>>>> Stashed changes
    }


@app.get("/")
async def root():
    return RedirectResponse(url="/docs")


@app.post("/v1/ingest", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_ingestion(
    files: list[UploadFile] = File(...),
<<<<<<< Updated upstream
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

=======
    tier: str = QueryParam("slow", description="instant | slow | global"),
    strategy: str | None = QueryParam(None, description="Override chunk strategy"),
    hypothetical_questions: bool = QueryParam(
        False, description="Add hypothetical questions at index time (slow/global)"
    ),
):
    """Upload and ingest one or more files into the RAG index.

    **tier** controls processing depth:
    - `instant` — fastest, text-only, no OCR.  For immediate chat RAG.
    - `slow` — full OCR + multimodal embedding.  Default.
    - `global` — maximum quality, all enhancements.  For batch processing.

    When `tier` is set, the tier's default chunk strategy and hypothetical-
    question settings are used unless explicitly overridden via `strategy`
    or `hypothetical_questions`.
    """
    try:
        parsed_tier = tier_from_str(tier)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    tmpdir = tempfile.mkdtemp()
>>>>>>> Stashed changes
    try:
        for upload in files:
<<<<<<< Updated upstream
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


@app.post("/v1/promote", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_promotion(body: PromotionRequest):
    source = Path(body.source_path)
    if not source.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Source file not found: {body.source_path!r}",
        )
    job = job_store.create(
        kind="promote",
        payload={
            "source_path": str(source.resolve()),
            "to_tier": body.to_tier.value,
            "delete_old_chunks": body.delete_old_chunks,
        },
        filenames=[source.name],
    )
    ingestion_worker.notify()
    return _public_job(job)


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
        raise HTTPException(
            status_code=409,
            detail="Running ingestion cannot be interrupted safely",
        )
    job_store.cancel(job_id)
    return _public_job(_get_job_or_404(job_id))
=======
            filename = upload.filename or f"upload_{uuid.uuid4().hex}"
            dest = Path(tmpdir) / filename
            content = await upload.read()
            async with aiofiles.open(dest, "wb") as f:
                await f.write(content)
            filenames.append(filename)

        # Use the tier system for proper per-tier behaviour
        stats = ingest_tier(tmpdir, tier=parsed_tier)

        # If caller explicitly supplied strategy or hypothetical_questions,
        # fall back to the legacy ingest_path which respects those args.
        if strategy is not None or hypothetical_questions:
            stats = ingest_path(
                tmpdir,
                strategy=strategy,
                add_hypothetical_questions=hypothetical_questions,
            )

        return {"status": "ok", "files": filenames, "tier": parsed_tier.value, "stats": stats}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
>>>>>>> Stashed changes


@app.post("/v1/promote")
async def promote(body: PromoteRequest):
    """Promote a document to a higher ingestion tier.

    Re-ingests the file at the target tier.  By default, old Milvus chunks
    for this file are deleted first so duplicates do not accumulate.

    Typical use: promote a chat-upload (instant) to the global index as a
    background job once the user closes the session.

    Example::

        POST /v1/promote
        {
          "source_path": "/data/uploads/report.pdf",
          "to_tier": "global",
          "delete_old_chunks": true
        }
    """
    try:
        to_tier = tier_from_str(body.to_tier)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        stats = promote_document(
            body.source_path,
            to_tier=to_tier,
            delete_old_chunks=body.delete_old_chunks,
        )
        return {"status": "ok", "stats": stats}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/v1/search", response_model=SearchResponse)
async def search_endpoint(body: SearchRequest):
<<<<<<< Updated upstream
    try:
        # Resolve enhancements — precedence: explicit string > tier > individual flags
        if body.enhancements is not None:
            enhancements = body.enhancements
        elif body.tier is not None:
            options = options_for_tier(body.tier)
            enhancements = ",".join(options.query_enhancements)
        else:
            enabled = []
            if body.hyde:
                enabled.append("hyde")
            if body.sub_queries:
                enabled.append("sub_queries")
            if body.stepback:
                enabled.append("stepback")
            enhancements = ",".join(enabled)

        # Pre-resolve enhanced queries for logging
        from pipeline.query import enhance_query as _enhance
        enhanced_queries = _enhance(body.query, enhancements=enhancements)
=======
    """Search the RAG index with optional query enhancements.

    **tier** sets search defaults (enhancements, reranker).  Explicit
    `enhancements` / `use_reranker` fields override tier defaults.

    Enhancement names: ``hyde``, ``sub_queries``, ``stepback``
    (comma-separated in `enhancements`).
    """
    try:
        # Resolve search options from tier defaults, then apply explicit overrides.
        enhancements_list: list[str] | None = None
        use_reranker = body.use_reranker
        use_chatbot_llm = False

        if body.tier is not None:
            try:
                t = tier_from_str(body.tier)
                tier_opts = options_for_tier(t)
                enhancements_list = list(tier_opts.query_enhancements)
                use_reranker = tier_opts.use_reranker
                use_chatbot_llm = tier_opts.use_chatbot_llm
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        # Explicit enhancements string overrides tier default.
        if body.enhancements is not None:
            enhancements_list = [
                s.strip() for s in body.enhancements.split(",") if s.strip()
            ]

        results = search(
            body.query,
            top_k=body.top_k,
            use_reranker=use_reranker,
            retrieval_mode=body.mode,
            enhancements=enhancements_list,
            use_chatbot_llm=use_chatbot_llm,
        )
>>>>>>> Stashed changes

        # Resolve reranker — tier overrides, individual flag is default
        use_reranker = body.use_reranker
        if body.tier is not None:
            options = options_for_tier(body.tier)
            use_reranker = options.use_reranker

        results, timing = search(
            body.query,
            top_k=body.top_k,
            use_reranker=use_reranker,
            retrieval_mode=body.mode,
            enhancements=enhancements,
            hierarchical=body.hierarchical,
            artifact_sources=body.artifact_sources,
        )
        formatted = [
            {
                "rank":       result.rank + 1,
                "score":      result.score,
                "method":     result.retrieval_method,
                "source":     result.chunk.source_path,
                "chunk_type": result.chunk.chunk_type.value,
                "text":       (result.chunk.window_text or result.chunk.text)[:1000],
                "has_image":  result.chunk.image_data is not None,
                "metadata":   result.chunk.metadata,
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
        # Enhance with embedding config info
        result = []
        for c in collections:
            name = c.get("name", "") if isinstance(c, dict) else str(c)
            info = {
                "name": name,
                "embedding_model": cfg.text_embedding_model,
                "embedding_dim": cfg.text_embedding_dim,
                "collection_name": cfg.text_collection if name == cfg.text_collection else name,
            }
            result.append(info)
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
