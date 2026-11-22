import logging
import math
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.errors import PyMongoError
from pymongo.database import Database

from app.api.schemas import (ApprovalRequest, DocumentOut, DocumentsResponse, SearchRequest,
                             SearchResponse, StatsResponse)
from app.api.security import require_secret
from app.config.settings import Settings, get_settings
from app.embeddings.gemini_embedder import (
    EmbeddingError,
    EmbeddingQuotaError,
    EmbeddingRateLimitError,
    GeminiEmbedder,
)
from app.retrieval.hybrid_search import hybrid_search
from app.vectorstore.mongo_client import (
    CHUNKS,
    DOCUMENTS,
    INACCESSIBLE_ITEMS,
    SYNC_LOCKS,
    SYNC_LOGS,
    MongoConfigError,
    get_db,
)
from app.vectorstore.vector_search import build_filter

log = logging.getLogger(__name__)
public = APIRouter()
internal = APIRouter(dependencies=[Depends(require_secret)])


# ---- dependencies (override in tests via app.dependency_overrides) ----
def get_database(settings: Settings = Depends(get_settings)) -> Database:
    try:
        return get_db(settings)
    except MongoConfigError as exc:
        raise HTTPException(503, str(exc))


@lru_cache
def _embedder() -> GeminiEmbedder:
    return GeminiEmbedder()


def get_embedder() -> GeminiEmbedder:
    return _embedder()


def close_embedder() -> None:
    if _embedder.cache_info().currsize:
        _embedder().close()
        _embedder.cache_clear()


# ---- public ----
@public.get("/health")
def health():
    """Liveness only. Reveals nothing about configuration."""
    return {"status": "ok", "service": "sahyog-college-rag"}


# ---- internal ----
@internal.get("/health/secure")
def health_secure(settings: Settings = Depends(get_settings)):
    mongo_ok, mongo_err = False, None
    try:
        get_db(settings).command("ping")
        mongo_ok = True
    except Exception as exc:
        mongo_err = type(exc).__name__
    return {
        "status": "ok" if mongo_ok else "degraded",
        "environment": settings.environment,
        "mongo_connected": mongo_ok, "mongo_error": mongo_err,
        "gemini_key_configured": bool(settings.gemini_api_key),
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "vector_search_enabled": settings.vector_search_enabled,
        "link_visible": settings.link_visible,
        "drive_folder_configured": bool(settings.drive_root_folder_id),
    }


@internal.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, db: Database = Depends(get_database),
           embedder: GeminiEmbedder = Depends(get_embedder),
           settings: Settings = Depends(get_settings)):
    filters = build_filter(
        req.branch,
        req.semester,
        req.subject,
        req.academic_year,
        req.doc_type,
    )
    try:
        has_approved_chunks = db[CHUNKS].find_one(
            {"approved": True}, {"_id": 1}
        ) is not None
    except PyMongoError as exc:
        log.error("MongoDB search preflight failed (%s)", type(exc).__name__)
        raise HTTPException(503, "MongoDB search service is unavailable") from exc
    if not has_approved_chunks:
        raise HTTPException(
            503,
            "No approved knowledge chunks are available; an administrator must approve indexed documents.",
        )

    try:
        out = hybrid_search(db, embedder, req.query, top_k=req.top_k, filters=filters,
                            history=req.history, settings=settings)
    except EmbeddingQuotaError as exc:
        headers = (
            {"Retry-After": str(max(1, math.ceil(exc.retry_after)))}
            if exc.retry_after is not None
            else None
        )
        raise HTTPException(
            429,
            "Gemini daily embedding quota is exhausted; retry after the quota resets or use keyword-only search.",
            headers=headers,
        ) from exc
    except EmbeddingRateLimitError as exc:
        headers = (
            {"Retry-After": str(max(1, math.ceil(exc.retry_after)))}
            if exc.retry_after is not None
            else None
        )
        raise HTTPException(
            429,
            "Gemini is rate limiting query embeddings; retry later.",
            headers=headers,
        ) from exc
    except EmbeddingError as exc:
        log.error("Embedding failure (%s)", type(exc).__name__)
        raise HTTPException(502, "Embedding service unavailable")
    except PyMongoError as exc:
        log.error("MongoDB retrieval failed (%s)", type(exc).__name__)
        raise HTTPException(
            503,
            "MongoDB retrieval is unavailable; verify database connectivity and search indexes.",
        ) from exc
    except Exception as exc:
        log.exception("Search failed")
        raise HTTPException(500, "Search failed") from exc
    return SearchResponse(count=len(out["results"]), **out)


def _doc_out(d: dict, link_visible: bool) -> DocumentOut:
    upd = d.get("updated_at")
    return DocumentOut(
        document_id=d["file_id"], file_id=d["file_id"], file_name=d.get("file_name", ""),
        folder_path=d.get("folder_path", ""), mime_type=d.get("mime_type", ""),
        status=d.get("status", "pending"), approved=bool(d.get("approved", False)),
        chunk_count=d.get("chunk_count", 0), error=d.get("error"),
        updated_at=upd.isoformat() if upd else None,
        source_url=d.get("source_url") if link_visible else None,
        source_type=d.get("source_type"),
        source_path=d.get("source_path"),
        metadata={
            key: d[key]
            for key in (
                "branch",
                "semester",
                "subject",
                "subject_code",
                "academic_year",
                "study_year",
                "unit",
                "chapter",
                "doc_type",
            )
            if d.get(key) is not None
        },
    )


@internal.get("/documents", response_model=DocumentsResponse)
def list_documents(status: Optional[str] = Query(None, pattern="^(indexed|failed|pending|deleted|duplicate)$"),
                   approved: Optional[bool] = None, q: Optional[str] = Query(None, max_length=100),
                   limit: int = Query(50, ge=1, le=200), skip: int = Query(0, ge=0),
                   db: Database = Depends(get_database), settings: Settings = Depends(get_settings)):
    flt: dict = {}
    if status:
        flt["status"] = status
    if approved is not None:
        flt["approved"] = approved
    if q:
        import re
        flt["file_name"] = {"$regex": re.escape(q), "$options": "i"}
    total = db[DOCUMENTS].count_documents(flt)
    cur = db[DOCUMENTS].find(flt).sort("updated_at", -1).skip(skip).limit(limit)
    return DocumentsResponse(total=total, items=[_doc_out(d, settings.link_visible) for d in cur])


@internal.patch("/documents/{file_id}/approval")
def set_approval(file_id: str, body: ApprovalRequest, db: Database = Depends(get_database)):
    doc = db[DOCUMENTS].find_one({"file_id": file_id})
    if not doc:
        raise HTTPException(404, "Document not found")
    if body.approved and doc.get("status") != "indexed":
        raise HTTPException(409, "Only indexed documents can be approved")
    db[DOCUMENTS].update_one({"file_id": file_id}, {"$set": {"approved": body.approved}})
    # chunks carry a denormalised copy of `approved` so vector/keyword filters stay fast
    res = db[CHUNKS].update_many({"document_id": file_id}, {"$set": {"approved": body.approved}})
    return {"file_id": file_id, "approved": body.approved, "chunks_updated": res.modified_count}


@internal.get("/stats", response_model=StatsResponse)
def stats(db: Database = Depends(get_database)):
    D = db[DOCUMENTS]
    last = db[SYNC_LOGS].find_one(sort=[("started_at", -1)])
    last_out = None
    if last:
        last_out = {"status": last.get("status"),
                    "started_at": last["started_at"].isoformat() if last.get("started_at") else None,
                    "finished_at": last["finished_at"].isoformat() if last.get("finished_at") else None,
                    "summary": last.get("summary")}
    sample = D.find_one({"status": "indexed"}) or {}
    return StatsResponse(
        total_documents=D.count_documents({"status": {"$ne": "deleted"}}),
        indexed_documents=D.count_documents({"status": "indexed"}),
        failed_documents=D.count_documents({"status": "failed"}),
        pending_documents=D.count_documents({"status": "pending"}),
        deleted_documents=D.count_documents({"status": "deleted"}),
        approved_documents=D.count_documents({"status": "indexed", "approved": True}),
        disabled_documents=D.count_documents({"status": "indexed", "approved": False}),
        total_chunks=db[CHUNKS].estimated_document_count(),
        embedding_model=sample.get("embedding_model"),
        embedding_dimensions=sample.get("embedding_dimensions"),
        last_sync=last_out)


@internal.get("/sync/status")
def sync_status(request: Request, db: Database = Depends(get_database),
                settings: Settings = Depends(get_settings)):
    last = db[SYNC_LOGS].find_one(sort=[("started_at", -1)])
    last_out = None
    next_sync_at = None
    if last:
        started = last.get("started_at")
        finished = last.get("finished_at")
        last_out = {
            "status": last.get("status"),
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "summary": last.get("summary"),
            "scan": last.get("scan"),
        }
        if settings.auto_sync_enabled and started:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            next_sync_at = (
                started + timedelta(minutes=settings.sync_interval_minutes)
            ).isoformat()

    current_state = request.app.state.sync_runner.status()
    lease = db[SYNC_LOCKS].find_one({"_id": "drive-sync"})
    if lease and lease.get("lease_until"):
        lease_until = lease["lease_until"]
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        if lease_until > datetime.now(timezone.utc) and not current_state["running"]:
            current_state = {
                "state": "running",
                "running": True,
                "source": "another_worker",
                "error_type": None,
                "summary": None,
            }

    return {
        "state": current_state,
        "last_sync": last_out,
        "schedule": {
            "mode": "external_cron",
            "enabled": settings.auto_sync_enabled,
            "interval_minutes": settings.sync_interval_minutes,
            "next_sync_at": next_sync_at,
        },
    }


@internal.get("/inaccessible")
def list_inaccessible(
    limit: int = Query(100, ge=1, le=500),
    db: Database = Depends(get_database),
):
    collection = db[INACCESSIBLE_ITEMS]
    items = list(
        collection.find({}, {"_id": 0})
        .sort("last_checked_at", -1)
        .limit(limit)
    )
    return {
        "total": collection.count_documents({}),
        "items": items,
    }


def _trigger_sync(request: Request, settings: Settings) -> dict:
    missing = settings.missing_for("mongo", "gemini", "drive")
    if missing:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Sync is not configured: {', '.join(missing)}",
        )
    runner = request.app.state.sync_runner
    if not runner.submit(settings):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A sync is already running in this service instance.",
        )
    return {"status": "accepted", "message": "Drive sync started."}


@internal.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def trigger_sync(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    return _trigger_sync(request, settings)


@internal.post("/sync/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_sync(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    # A full incremental scan retries inaccessible targets and failed documents,
    # while unchanged successful documents remain skipped.
    return _trigger_sync(request, settings)
