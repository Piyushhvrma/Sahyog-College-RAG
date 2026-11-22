"""Incremental local-folder ingestion using the existing document pipeline."""
import logging
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from pymongo.database import Database

from app.config.settings import Settings, get_settings
from app.drive.folder_scanner import DriveFile
from app.drive.sync import SyncSummary, _sync_lease, process_file, utcnow
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.ingestion.local_source import (
    LocalFileClient,
    LocalScanResult,
    scan_local_folder,
)
from app.ingestion.metadata import extract_metadata
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS, SYNC_LOGS

log = logging.getLogger(__name__)


def _existing_local_documents(
    db: Database, files: list[DriveFile]
) -> dict[str, dict]:
    if not files:
        return {}
    return {
        doc["file_id"]: doc
        for doc in db[DOCUMENTS].find(
            {"file_id": {"$in": [file.file_id for file in files]}}
        )
    }


def _unchanged(
    file: DriveFile, existing: dict | None, embedder: GeminiEmbedder
) -> bool:
    return bool(
        existing
        and existing.get("status") in ("indexed", "duplicate")
        and existing.get("change_token") == file.change_token
        and existing.get("embedding_model") == embedder.model
        and existing.get("embedding_dimensions") == embedder.dimensions
    )


def estimate_local_work(
    scan: LocalScanResult,
    existing_docs: dict[str, dict],
    embedder: GeminiEmbedder,
) -> dict[str, int]:
    """Estimate incremental work without embedding or changing MongoDB."""
    duplicate_paths = {
        item["path"]
        for item in scan.duplicates
    }
    counts = {"new": 0, "modified": 0, "unchanged": 0, "duplicates": 0}
    for file in scan.files:
        existing = existing_docs.get(file.file_id)
        if _unchanged(file, existing, embedder):
            counts["unchanged"] += 1
        elif file.raw["source_path"] in duplicate_paths:
            counts["duplicates"] += 1
        elif existing:
            counts["modified"] += 1
        else:
            counts["new"] += 1
    return counts


def _summary_from_scan(scan: LocalScanResult) -> SyncSummary:
    summary = SyncSummary(
        scanned=len(scan.files),
        unsupported=len(scan.unsupported),
        archives_scanned=scan.archives,
        archive_files=scan.archive_files,
        inaccessible=len(scan.inaccessible) + len(scan.oversized),
        scan_errors=len(scan.errors),
        scan_status=scan.status,
    )
    summary.failures.extend(
        {"file": item["path"], "error": item["reason"]}
        for item in scan.inaccessible
    )
    summary.failures.extend(
        {
            "file": item["path"],
            "error": (
                f"File size {item['size_bytes']} exceeds limit "
                f"{item['limit_bytes']} bytes"
            ),
        }
        for item in scan.oversized
    )
    summary.failures.extend(scan.errors)
    return summary


def _write_failure(
    db: Database,
    file: DriveFile,
    existing: dict | None,
    error: Exception,
) -> dict:
    message = f"{type(error).__name__}: {error}"[:500]
    metadata = extract_metadata(file.folder_path, file.name)
    db[DOCUMENTS].update_one(
        {"file_id": file.file_id},
        {
            "$set": {
                "file_id": file.file_id,
                "file_name": file.name,
                "mime_type": file.mime_type,
                "kind": file.kind,
                "folder_path": file.folder_path,
                "source_url": None,
                "source_type": "local",
                "source_path": file.raw["source_path"],
                "source_file_hash": file.raw["source_file_hash"],
                "change_token": file.change_token,
                "status": "failed",
                "error": message,
                "updated_at": utcnow(),
                **metadata,
                **({} if existing else {"approved": False, "chunk_count": 0}),
            },
            "$setOnInsert": {"created_at": utcnow()},
        },
        upsert=True,
    )
    return {"file": file.name, "file_id": file.file_id, "error": message}


def run_local_ingestion(
    db: Database,
    root: str | Path,
    embedder: GeminiEmbedder,
    settings: Settings | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    progress: Callable[[str], None] = print,
) -> SyncSummary:
    """Ingest files without Drive access and never reconcile missing local files."""
    settings = settings or get_settings()
    scan = scan_local_folder(root, settings)
    summary = _summary_from_scan(scan)
    existing_docs = _existing_local_documents(db, scan.files)

    if dry_run:
        estimates = estimate_local_work(scan, existing_docs, embedder)
        summary.new = estimates["new"]
        summary.updated = estimates["modified"]
        summary.skipped_unchanged = estimates["unchanged"]
        summary.duplicates = estimates["duplicates"]
        summary.processed_count = (
            estimates["new"] + estimates["modified"] + estimates["duplicates"]
        )
        summary.finished_at = utcnow().isoformat()
        progress(
            "Dry run only: no embeddings or MongoDB writes. "
            f"New {summary.new}, modified {summary.updated}, "
            f"unchanged {summary.skipped_unchanged}, duplicates {summary.duplicates}."
        )
        return summary

    with _sync_lease(db):
        log_id = db[SYNC_LOGS].insert_one(
            {
                "started_at": utcnow(),
                "status": "running",
                "source": "local",
                "embedding_model": embedder.model,
                "embedding_dimensions": embedder.dimensions,
            }
        ).inserted_id
        processed = 0
        local_client = LocalFileClient(settings)
        with tempfile.TemporaryDirectory(prefix="sahyog_local_rag_") as tmp:
            workdir = Path(tmp)
            for index, file in enumerate(scan.files, start=1):
                existing = existing_docs.get(file.file_id)
                if _unchanged(file, existing, embedder):
                    summary.skipped_unchanged += 1
                    continue
                if limit is not None and processed >= limit:
                    break

                processed += 1
                summary.processed_count += 1
                progress(
                    f"[{index}/{len(scan.files)}] "
                    f"{file.raw['source_path']}"
                )
                try:
                    outcome, chunks_written = process_file(
                        file,
                        local_client,
                        db,
                        embedder,
                        settings,
                        existing,
                        workdir,
                        auto_approve=False,
                    )
                    summary.chunks_written += chunks_written
                    if outcome == "new":
                        summary.new += 1
                    elif outcome == "updated":
                        summary.updated += 1
                    else:
                        summary.duplicates += 1
                    progress(f"  -> {outcome} ({chunks_written} chunks)")
                except Exception as exc:
                    summary.failed += 1
                    failure = _write_failure(db, file, existing, exc)
                    summary.failures.append(failure)
                    log.error(
                        "Local ingestion failed for %s: %s\n%s",
                        file.raw["source_path"],
                        failure["error"],
                        traceback.format_exc(),
                    )
                    progress(f"  -> FAILED: {failure['error']}")

        summary.approved_count = db[DOCUMENTS].count_documents(
            {"status": "indexed", "approved": True}
        )
        summary.pending_count = db[DOCUMENTS].count_documents(
            {"status": "indexed", "approved": False}
        )
        summary.finished_at = utcnow().isoformat()
        status = (
            "completed"
            if scan.status == "complete" and not summary.failed
            else "completed_with_errors"
        )
        db[SYNC_LOGS].update_one(
            {"_id": log_id},
            {
                "$set": {
                    "finished_at": utcnow(),
                    "status": status,
                    "summary": summary.as_dict(),
                    "scan": {
                        "status": scan.status,
                        "started_at": scan.started_at,
                        "finished_at": scan.finished_at,
                        "supported_files": len(scan.files),
                        "archives_scanned": scan.archives,
                        "archive_supported_files": scan.archive_files,
                        "unsupported_files": scan.unsupported,
                        "duplicates": scan.duplicates,
                        "inaccessible_items": scan.inaccessible,
                        "oversized_files": scan.oversized,
                        "errors": scan.errors,
                    },
                }
            },
        )
    return summary
