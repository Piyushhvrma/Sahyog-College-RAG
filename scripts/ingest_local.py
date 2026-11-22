"""Incrementally ingest the configured local knowledge folder (never uses Drive)."""
import argparse
import json

from app.config.settings import get_settings
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.ingestion.local_source import (
    LocalKnowledgeFolderError,
    scan_local_folder,
)
from app.ingestion.local_sync import run_local_ingestion
from app.vectorstore.mongo_client import get_db, init_collections


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm MongoDB writes (required by the one-click launcher)",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")

    settings = get_settings()
    missing = settings.missing_for("mongo", "gemini", "local")
    if missing:
        print(f"ERROR: Missing required settings: {', '.join(missing)}")
        return 2
    try:
        scan = scan_local_folder(settings.local_knowledge_path, settings)
    except LocalKnowledgeFolderError as exc:
        print(f"ERROR: {exc}")
        return 2
    if not scan.files:
        print(
            "No supported local files found. Nothing was written. "
            "Add materials to LOCAL_KNOWLEDGE_DIR first."
        )
        return 2
    print(
        "Local scan: "
        f"{len(scan.files)} supported, {len(scan.unsupported)} unsupported, "
        f"{len(scan.duplicates)} byte-identical duplicates, "
        f"{len(scan.inaccessible)} unreadable, {len(scan.oversized)} oversized "
        f"({scan.status})."
    )
    if scan.inaccessible or scan.oversized or scan.errors:
        print("Warnings from the scan:")
        for item in scan.inaccessible:
            print(f"  unreadable {item['path']}: {item['reason']}")
        for item in scan.oversized:
            print(f"  oversized {item['path']} ({item['size_bytes']} bytes)")
        for item in scan.errors:
            print(f"  error {item['path']}: {item['reason']}")

    if not args.yes:
        answer = input(
            f"Ingest {len(scan.files)} supported files into MongoDB? "
            "This may call Gemini and write documents/chunks. [y/N]: "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Ingestion cancelled; no MongoDB writes were made.")
            return 0

    try:
        db = get_db(settings)
        db.command("ping")
        init_collections(db)
        summary = run_local_ingestion(
            db,
            settings.local_knowledge_path,
            GeminiEmbedder(settings),
            settings=settings,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"ERROR: Local ingestion stopped ({type(exc).__name__}).")
        return 1
    print("\n=== Local ingestion summary ===")
    print(json.dumps(summary.as_dict(), indent=2))
    return 1 if summary.failed or summary.scan_status != "complete" else 0


if __name__ == "__main__":
    raise SystemExit(run())
