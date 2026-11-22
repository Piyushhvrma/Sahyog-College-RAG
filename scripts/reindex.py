"""Re-extract and RE-EMBED every document (use after changing EMBEDDING_MODEL / EMBEDDING_DIMENSIONS).
Remember to recreate the Atlas vector index if dimensions changed. Costs Gemini quota.
  python -m scripts.reindex --yes"""
import sys

from scripts.ingest import run

if __name__ == "__main__":
    if "--yes" not in sys.argv:
        print("This re-embeds EVERYTHING. Re-run with --yes to confirm.")
        raise SystemExit(2)
    sys.argv.remove("--yes")
    raise SystemExit(run(force=True))
