"""Drive -> extract -> clean -> chunk -> embed -> MongoDB, incrementally.

Re-processing rules (a file is skipped when ALL hold):
  * it already exists with status 'indexed'
  * its Drive change token (md5 / modifiedTime) is unchanged
  * it was embedded with the current EMBEDDING_MODEL + EMBEDDING_DIMENSIONS
Use force=True (scripts.reindex) to re-embed everything, e.g. after a model change.
"""
import logging
import hashlib
import tempfile
import threading
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.database import Database

from app.config.settings import Settings, get_settings
from app.drive.client import DriveClient, EXPORT_MAP
from app.drive.folder_scanner import DriveFile, scan_folder
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.ingestion.chunker import chunk_pages
from app.ingestion.cleaner import clean_pages
from app.ingestion.loader import ExtractionError, load_document
from app.ingestion.metadata import content_hash, extract_metadata
from app.vectorstore.mongo_client import (
    CHUNKS,
    DOCUMENTS,
    INACCESSIBLE_ITEMS,
    SYNC_LOCKS,
    SYNC_LOGS,
)

log = logging.getLogger(__name__)
SYNC_LEASE_SECONDS = 120
SYNC_HEARTBEAT_SECONDS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SyncSummary:
    scanned: int = 0
    new: int = 0
    updated: int = 0
    skipped_unchanged: int = 0
    duplicates: int = 0
    failed: int = 0
    deleted: int = 0
    chunks_written: int = 0
    unsupported: int = 0
    archives_scanned: int = 0
    archive_files: int = 0
    inaccessible: int = 0
    transient_failures: int = 0
    scan_errors: int = 0
    processed_count: int = 0
    approved_count: int = 0
    pending_count: int = 0
    scan_status: str = "not_started"
    started_at: str = field(default_factory=lambda: utcnow().isoformat())
    finished_at: Optional[str] = None
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        result = self.__dict__.copy()
        result.update(
            discovered_count=self.scanned,
            unchanged_count=self.skipped_unchanged,
            failed_count=self.failed,
            inaccessible_count=self.inaccessible,
        )
        return result


def _ext_for(f: DriveFile) -> str:
    if f.mime_type in EXPORT_MAP:
        return EXPORT_MAP[f.mime_type][1]
    return Path(f.name).suffix or f".{f.kind}"


def process_file(f: DriveFile, client: DriveClient, db: Database, embedder: GeminiEmbedder,
                 settings: Settings, existing: Optional[dict], workdir: Path,
                 auto_approve: Optional[bool] = None) -> tuple[str, int]:
    """Process one Drive file. Returns (outcome, chunks_written). Raises on failure."""
    local = workdir / f"{f.file_id}{_ext_for(f)}"
    client.download(f.raw, local)
    pages = clean_pages(load_document(local, f.kind, settings))
    local.unlink(missing_ok=True)

    full_text = "\n\n".join(p.text for p in pages)
    doc_hash = content_hash(full_text)
    now = utcnow()
    meta = extract_metadata(f.folder_path, f.name, full_text)
    source_fields = {}
    if f.raw.get("source_type") == "local":
        source_fields = {
            "source_type": "local",
            "source_path": f.raw["source_path"],
            "source_file_hash": f.raw["source_file_hash"],
            "local_modified_time": f.modified_time,
        }
    base = {
        "file_id": f.file_id, "file_name": f.name, "mime_type": f.mime_type, "kind": f.kind,
        "folder_path": f.folder_path, "source_url": f.web_link,
        "drive_modified_time": (
            None if f.raw.get("source_type") == "local" else f.modified_time
        ),
        "change_token": f.change_token,
        "doc_hash": doc_hash, "page_count": len(pages), "ocr_pages": sum(p.ocr for p in pages),
        **meta, **source_fields, **embedder.meta, "updated_at": now, "error": None,
    }

    # Cross-file duplicate detection (same content uploaded twice).
    dup = db[DOCUMENTS].find_one({"doc_hash": doc_hash, "file_id": {"$ne": f.file_id},
                                  "status": "indexed"})
    if dup:
        db[DOCUMENTS].update_one({"file_id": f.file_id}, {
            "$set": {**base, "status": "duplicate", "duplicate_of": dup["file_id"],
                     "chunk_count": 0, "approved": False},
            "$setOnInsert": {"created_at": now}}, upsert=True)
        db[CHUNKS].delete_many({"document_id": f.file_id})
        return "duplicate", 0

    chunks = chunk_pages(pages, settings.chunk_target_tokens, settings.chunk_overlap_tokens)
    if not chunks:
        raise ExtractionError("Document produced no chunks")
    vectors = embedder.embed_documents([c.text for c in chunks], [f.name] * len(chunks))

    version = int(now.timestamp() * 1000)
    if existing and "approved" in existing:
        approved = existing["approved"]
    else:
        approved = settings.auto_approve if auto_approve is None else auto_approve
    rows = []
    for c, vec in zip(chunks, vectors):
        rows.append({
            "_id": f"{f.file_id}:{version}:{c.chunk_index}",
            "document_id": f.file_id, "file_id": f.file_id, "file_name": f.name,
            "chunk_index": c.chunk_index, "page_number": c.page_number, "heading": c.heading,
            "section_label": c.label, "text": c.text, "content_hash": content_hash(c.text),
            "folder_path": f.folder_path, "source_url": f.web_link,
            "branch": meta["branch"], "semester": meta["semester"], "subject": meta["subject"],
            "subject_code": meta["subject_code"], "academic_year": meta["academic_year"],
            "study_year": meta["study_year"], "unit": meta["unit"],
            "chapter": meta["chapter"], "doc_type": meta["doc_type"],
            **source_fields, "approved": approved, "version": version,
            "embedding": vec, **embedder.meta, "created_at": now,
        })
    # Insert new version first, then delete old ones: searches never see an empty document.
    db[CHUNKS].insert_many(rows, ordered=False)
    db[CHUNKS].delete_many({"document_id": f.file_id, "version": {"$ne": version}})
    db[DOCUMENTS].update_one({"file_id": f.file_id}, {
        "$set": {**base, "status": "indexed", "approved": approved, "chunk_count": len(rows),
                 "index_version": version, "indexed_at": now, "duplicate_of": None},
        "$setOnInsert": {"created_at": now}}, upsert=True)
    return ("updated" if existing else "new"), len(rows)


class SyncAlreadyRunningError(RuntimeError):
    pass


@contextmanager
def _sync_lease(db: Database):
    owner = uuid.uuid4().hex
    now = utcnow()
    lease_until = now + timedelta(seconds=SYNC_LEASE_SECONDS)
    locks = db[SYNC_LOCKS]
    try:
        lock = locks.find_one_and_update(
            {
                "_id": "drive-sync",
                "$or": [
                    {"lease_until": {"$lte": now}},
                    {"lease_until": {"$exists": False}},
                ],
            },
            {"$set": {"owner": owner, "lease_until": lease_until}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise SyncAlreadyRunningError(
            "A Drive sync is already running"
        ) from exc
    if not lock or lock.get("owner") != owner:
        raise SyncAlreadyRunningError("A Drive sync is already running")

    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(SYNC_HEARTBEAT_SECONDS):
            try:
                updated = locks.update_one(
                    {"_id": "drive-sync", "owner": owner},
                    {
                        "$set": {
                            "lease_until": utcnow()
                            + timedelta(seconds=SYNC_LEASE_SECONDS)
                        }
                    },
                )
                if updated.matched_count != 1:
                    log.error("Drive sync lease was lost")
                    return
            except Exception:
                log.exception("Failed to renew Drive sync lease")

    heartbeat = threading.Thread(
        target=renew,
        name="sahyog-drive-sync-lease",
        daemon=True,
    )
    heartbeat.start()
    try:
        yield
    finally:
        stopped.set()
        heartbeat.join(timeout=SYNC_HEARTBEAT_SECONDS + 1)
        try:
            locks.delete_one({"_id": "drive-sync", "owner": owner})
        except Exception:
            log.exception("Failed to release Drive sync lease")


def run_sync(
    db: Database,
    client: DriveClient,
    embedder: GeminiEmbedder,
    settings: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
    only_failed: bool = False,
    limit: Optional[int] = None,
    progress: Callable[[str], None] = print,
) -> SyncSummary:
    with _sync_lease(db):
        return _run_sync(
            db,
            client,
            embedder,
            settings=settings,
            force=force,
            dry_run=dry_run,
            only_failed=only_failed,
            limit=limit,
            progress=progress,
        )


def _run_sync(db: Database, client: DriveClient, embedder: GeminiEmbedder,
              settings: Settings | None = None, force: bool = False, dry_run: bool = False,
              only_failed: bool = False, limit: Optional[int] = None,
              progress: Callable[[str], None] = print) -> SyncSummary:
    settings = settings or get_settings()
    summary = SyncSummary()
    log_id = None
    if not dry_run:
        log_id = db[SYNC_LOGS].insert_one({
            "started_at": utcnow(), "status": "running", "force": force,
            "embedding_model": embedder.model, "embedding_dimensions": embedder.dimensions}).inserted_id

    progress("Scanning Drive …")
    scan = scan_folder(client, settings.drive_root_folder_id)
    summary.scanned = len(scan.files)
    summary.unsupported = len(scan.unsupported)
    summary.inaccessible = len(scan.inaccessible)
    summary.transient_failures = len(scan.transient_failures)
    summary.scan_errors = len(scan.errors)
    summary.scan_status = scan.status
    progress(f"  {len(scan.files)} supported files, {len(scan.unsupported)} unsupported, "
             f"{len(scan.inaccessible)} inaccessible, {len(scan.transient_failures)} transient failures, "
             f"{len(scan.errors)} scan errors, {scan.folders_scanned} folders ({scan.status})")

    if not dry_run:
        inaccessible = db[INACCESSIBLE_ITEMS]
        for item in scan.inaccessible:
            if not item.get("id"):
                continue
            key = hashlib.sha256(
                f"{item['id']}\0{item.get('path', '')}".encode("utf-8")
            ).hexdigest()
            inaccessible.update_one(
                {"_id": key},
                {
                    "$set": {
                        **item,
                        "status": "inaccessible",
                    },
                    "$setOnInsert": {"first_seen_at": utcnow()},
                },
                upsert=True,
            )
        if scan.resolved_shortcut_ids:
            inaccessible.delete_many(
                {"id": {"$in": list(set(scan.resolved_shortcut_ids))}}
            )

    existing_docs = {d["file_id"]: d for d in db[DOCUMENTS].find({})}
    processed = 0
    with tempfile.TemporaryDirectory(prefix="sahyog_rag_") as tmp:
        workdir = Path(tmp)
        for i, f in enumerate(scan.files, start=1):
            ex = existing_docs.get(f.file_id)
            unchanged = (ex and ex.get("status") in ("indexed", "duplicate")
                         and ex.get("change_token") == f.change_token
                         and ex.get("embedding_model") == embedder.model
                         and ex.get("embedding_dimensions") == embedder.dimensions)
            if only_failed and not (ex and ex.get("status") == "failed"):
                continue
            if unchanged and not force:
                summary.skipped_unchanged += 1
                continue
            if limit is not None and processed >= limit:
                break
            processed += 1
            summary.processed_count += 1
            label = f"[{i}/{len(scan.files)}] {f.folder_path}/{f.name}"
            if dry_run:
                progress(f"  WOULD PROCESS {label}")
                summary.new += 0 if ex else 1
                summary.updated += 1 if ex else 0
                continue
            try:
                progress(f"  {label}")
                outcome, n = process_file(f, client, db, embedder, settings, ex, workdir)
                summary.chunks_written += n
                if outcome == "new":
                    summary.new += 1
                elif outcome == "updated":
                    summary.updated += 1
                else:
                    summary.duplicates += 1
                progress(f"    -> {outcome} ({n} chunks)")
            except Exception as exc:  # one bad file must never kill the job
                summary.failed += 1
                msg = f"{type(exc).__name__}: {exc}"[:500]
                summary.failures.append({"file": f.name, "file_id": f.file_id, "error": msg})
                log.error("Failed %s: %s\n%s", f.name, msg, traceback.format_exc())
                progress(f"    -> FAILED: {msg}")
                db[DOCUMENTS].update_one({"file_id": f.file_id}, {
                    "$set": {"file_id": f.file_id, "file_name": f.name, "mime_type": f.mime_type,
                             "kind": f.kind, "folder_path": f.folder_path, "source_url": f.web_link,
                             "status": "failed", "error": msg, "updated_at": utcnow(),
                             **({} if ex else {"approved": False, "chunk_count": 0})},
                    "$setOnInsert": {"created_at": utcnow()}}, upsert=True)

    # Deleted-file handling. Skipped if any folder was inaccessible (avoids false deletions) or
    # when running a partial job.
    full_run = not (only_failed or limit is not None or dry_run)
    if full_run and scan.status == "complete":
        live_ids = {f.file_id for f in scan.files}
        for fid, d in existing_docs.items():
            if fid not in live_ids and d.get("status") != "deleted":
                db[CHUNKS].delete_many({"document_id": fid})
                db[DOCUMENTS].update_one({"file_id": fid}, {"$set": {
                    "status": "deleted", "chunk_count": 0, "approved": False, "updated_at": utcnow()}})
                summary.deleted += 1
                progress(f"  removed from Drive -> deleted: {d.get('file_name')}")
    elif full_run:
        progress("  Skipping deletion pass: some folders were inaccessible.")

    summary.approved_count = db[DOCUMENTS].count_documents(
        {"status": "indexed", "approved": True}
    )
    summary.pending_count = db[DOCUMENTS].count_documents(
        {"status": "indexed", "approved": False}
    )
    summary.finished_at = utcnow().isoformat()

    if log_id is not None:
        run_status = (
            "completed"
            if scan.status == "complete" and not summary.failed
            else "completed_with_errors"
        )
        db[SYNC_LOGS].update_one({"_id": log_id}, {"$set": {
            "finished_at": utcnow(),
            "status": run_status,
            "summary": summary.as_dict(),
            "scan": {
                "status": scan.status,
                "started_at": scan.started_at,
                "finished_at": scan.finished_at,
                "folders_scanned": scan.folders_scanned,
                "supported_files": len(scan.files),
                "unsupported_files": len(scan.unsupported),
                "inaccessible_items": scan.inaccessible[:500],
                "transient_failures": scan.transient_failures[:500],
                "errors": scan.errors[:500],
            },
            "inaccessible": scan.inaccessible[:50],
        }})
    return summary
