"""Incremental sync (run after adding/changing/removing Drive files). Same engine as ingest.
  python -m scripts.sync_drive"""
from scripts.ingest import run

if __name__ == "__main__":
    raise SystemExit(run(force=False))
