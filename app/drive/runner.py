"""Runs admin-triggered Drive syncs outside FastAPI's request event loop."""
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.config.settings import Settings

log = logging.getLogger(__name__)


class SyncRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._future: Future | None = None
        self._state: dict[str, Any] = {
            "state": "idle",
            "error_type": None,
            "summary": None,
        }

    def submit(self, settings: Settings) -> bool:
        with self._lock:
            if self._future and not self._future.done():
                return False
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="sahyog-drive-sync",
                )
            self._state = {
                "state": "queued",
                "error_type": None,
                "summary": None,
            }
            self._future = self._executor.submit(self._run, settings)
            return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._state,
                "running": bool(
                    self._future and not self._future.done()
                ),
            }

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, settings: Settings) -> None:
        with self._lock:
            self._state["state"] = "running"
        try:
            missing = settings.missing_for("mongo", "gemini", "drive")
            if missing:
                raise RuntimeError(
                    f"Sync configuration is missing: {', '.join(missing)}"
                )

            from app.drive.client import DriveClient
            from app.drive.sync import run_sync
            from app.embeddings.gemini_embedder import GeminiEmbedder
            from app.vectorstore.mongo_client import get_db, init_collections

            db = get_db(settings)
            init_collections(db)
            summary = run_sync(
                db,
                DriveClient(settings, interactive_auth=False),
                GeminiEmbedder(settings),
                settings=settings,
            )
            state = (
                "completed"
                if summary.scan_status == "complete" and not summary.failed
                else "completed_with_errors"
            )
            with self._lock:
                self._state.update(
                    state=state,
                    error_type=None,
                    summary=summary.as_dict(),
                )
        except Exception as exc:
            log.exception("Admin-triggered Drive sync failed")
            with self._lock:
                self._state.update(
                    state="failed",
                    error_type=type(exc).__name__,
                    summary=None,
                )
