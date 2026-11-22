"""Read-only recursive local knowledge scan; never embeds or writes to MongoDB."""
from collections import Counter

from app.config.settings import get_settings
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.ingestion.local_source import (
    LocalKnowledgeFolderError,
    scan_local_folder,
)
from app.ingestion.local_sync import estimate_local_work
from app.vectorstore.mongo_client import DOCUMENTS, get_db


def main() -> int:
    settings = get_settings()
    try:
        scan = scan_local_folder(settings.local_knowledge_path, settings)
    except LocalKnowledgeFolderError as exc:
        print(f"ERROR: {exc}")
        return 2

    existing_docs = {}
    mongo_read_error = None
    if settings.mongo_uri:
        try:
            db = get_db(settings)
            db.command("ping")
            file_ids = [file.file_id for file in scan.files]
            if file_ids:
                existing_docs = {
                    doc["file_id"]: doc
                    for doc in db[DOCUMENTS].find(
                        {"file_id": {"$in": file_ids}},
                        {
                            "file_id": 1,
                            "status": 1,
                            "change_token": 1,
                            "embedding_model": 1,
                            "embedding_dimensions": 1,
                        },
                    )
                }
        except Exception as exc:
            print(
                "WARNING: Could not read existing document state from MongoDB "
                f"({type(exc).__name__}); the local file scan will still be shown."
            )
            mongo_read_error = type(exc).__name__
    else:
        print(
            "MongoDB is not configured; estimate treats readable unique files "
            "as new candidates."
        )

    print(f"Local folder: {scan.root}")
    print(f"Scan status: {scan.status}")
    print(f"Supported files: {len(scan.files)}")
    format_counts = Counter(file.kind for file in scan.files)
    if format_counts:
        print(
            "Supported formats: "
            + ", ".join(
                f"{kind}={count}" for kind, count in sorted(format_counts.items())
            )
        )
    print(f"ZIP archives (including nested): {scan.archives}")
    print(f"Supported files inside ZIPs: {scan.archive_files}")
    print(f"Unsupported files: {len(scan.unsupported)}")
    print(f"Byte-identical duplicates: {len(scan.duplicates)}")
    print(f"Unreadable paths: {len(scan.inaccessible)}")
    print(f"Files over MAX_DOWNLOAD_MB: {len(scan.oversized)}")
    if mongo_read_error:
        print("Incremental ingestion estimate unavailable; MongoDB could not be read.")
    else:
        estimates = estimate_local_work(
            scan, existing_docs, GeminiEmbedder(settings)
        )
        ingest_candidates = (
            estimates["new"] + estimates["modified"] + estimates["duplicates"]
        )
        print(
            f"Estimated ingest candidates: {ingest_candidates} files. "
            f"New {estimates['new']}, modified {estimates['modified']}, "
            f"{estimates['unchanged']} unchanged, {estimates['duplicates']} duplicate."
        )
    print("No Gemini embeddings were requested and no MongoDB writes were made.")

    if scan.files:
        print("\nDiscovered supported files:")
        for file in scan.files:
            print(f"  {file.raw['source_path']}")
    if scan.unsupported:
        print("\nUnsupported files:")
        for item in scan.unsupported:
            print(f"  {item['path']} ({item['file_type']})")
    if scan.duplicates:
        print("\nByte-identical duplicates:")
        for item in scan.duplicates:
            print(f"  {item['path']} -> same bytes as {item['duplicate_of']}")
    if scan.inaccessible:
        print("\nUnreadable paths:")
        for item in scan.inaccessible:
            print(f"  {item['path']}: {item['reason']}")
    if scan.oversized:
        print("\nOversized files:")
        for item in scan.oversized:
            print(f"  {item['path']} ({item['size_bytes']} bytes)")
    if scan.errors:
        print("\nScan errors:")
        for item in scan.errors:
            print(f"  {item}")

    if not scan.files:
        print(
            "\nNo supported local files were found. Add the downloaded materials "
            "to LOCAL_KNOWLEDGE_DIR before running ingestion."
        )
        return 2
    return 1 if mongo_read_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
