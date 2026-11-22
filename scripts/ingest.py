"""Full ingestion (safe to re-run; unchanged files are skipped).
  python -m scripts.ingest --dry-run      # show what would be processed
  python -m scripts.ingest --limit 3      # try 3 files first
  python -m scripts.ingest                # everything new/changed
  python -m scripts.ingest --only-failed  # retry failures"""
import argparse
import json

from app.drive.client import DriveClient
from app.drive.sync import run_sync
from app.embeddings.gemini_embedder import GeminiEmbedder
from app.vectorstore.mongo_client import get_db, init_collections
from scripts._common import bootstrap


def run(force: bool = False) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-failed", action="store_true")
    args = ap.parse_args()
    s = bootstrap("mongo", "gemini", "drive")
    db = get_db(s)
    init_collections(db)
    summary = run_sync(db, DriveClient(s), GeminiEmbedder(s), s, force=force, dry_run=args.dry_run,
                       only_failed=args.only_failed, limit=args.limit)
    print("\n=== Summary ===")
    d = summary.as_dict()
    failures = d.pop("failures")
    print(json.dumps(d, indent=2))
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  - {f['file']}: {f['error']}")
    return 1 if summary.failed or summary.scan_status != "complete" else 0


if __name__ == "__main__":
    raise SystemExit(run())
