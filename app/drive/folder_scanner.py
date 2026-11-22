"""Recursive Drive scanner: pagination, shortcuts, loop protection, inaccessible-file logging."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from googleapiclient.errors import HttpError

from app.drive.client import (
    EXPORT_MAP,
    FOLDER_MIME,
    SHORTCUT_MIME,
    DriveClient,
    is_inaccessible_error,
    is_transient_drive_error,
)

log = logging.getLogger(__name__)

SUPPORTED_MIMES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/vnd.google-apps.document": "gdoc",
    "application/vnd.google-apps.spreadsheet": "gsheet",
    "application/vnd.google-apps.presentation": "gslides",
}


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    kind: str                      # pdf/docx/pptx/txt/csv/gdoc/gsheet/gslides
    folder_path: str               # e.g. "CSE/Sem 5/PYQ"
    modified_time: Optional[str]
    md5: Optional[str]
    size: int
    web_link: Optional[str]
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def change_token(self) -> str:
        """Drive checksum or local source hash; fall back to modified time."""
        return (
            self.raw.get("source_file_hash")
            or self.md5
            or self.modified_time
            or ""
        )


@dataclass
class ScanResult:
    files: list[DriveFile] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    inaccessible: list[dict] = field(default_factory=list)
    transient_failures: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    resolved_shortcut_ids: list[str] = field(default_factory=list)
    folders_scanned: int = 0
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = None
    status: str = "running"


def scan_folder(client: DriveClient, root_folder_id: str) -> ScanResult:
    result = ScanResult()
    visited: set[tuple[str, Optional[str]]] = set()
    seen_files: set[str] = set()

    def walk(folder_id: str, path: str, resource_key: Optional[str] = None) -> None:
        visit = (folder_id, resource_key)
        if visit in visited:  # protects against shortcut loops
            return
        visited.add(visit)
        result.folders_scanned += 1
        try:
            if resource_key:
                children = list(client.list_children(folder_id, resource_key=resource_key))
            else:
                children = list(client.list_children(folder_id))
        except Exception as exc:
            _record_scan_error(result, exc, folder_id, path.rsplit("/", 1)[-1] or folder_id, path)
            return

        for item in children:
            item = _resolve_shortcut(client, item, result, path)
            if item is None:
                continue
            mime = item["mimeType"]
            if mime == FOLDER_MIME:
                walk(
                    item["id"],
                    f"{path}/{item['name']}".strip("/"),
                    item.get("resourceKey"),
                )
            elif mime in SUPPORTED_MIMES:
                if item["id"] in seen_files:
                    continue
                seen_files.add(item["id"])
                result.files.append(DriveFile(
                    file_id=item["id"], name=item["name"], mime_type=mime,
                    kind=SUPPORTED_MIMES[mime], folder_path=path,
                    modified_time=item.get("modifiedTime"), md5=item.get("md5Checksum"),
                    size=int(item.get("size") or 0), web_link=item.get("webViewLink"), raw=item,
                ))
            else:
                result.unsupported.append({"id": item["id"], "name": item["name"],
                                           "mime": mime, "path": path})

    walk(root_folder_id, "")
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.status = (
        "complete"
        if not result.inaccessible
        and not result.transient_failures
        and not result.errors
        else "partial"
    )
    return result


def _resolve_shortcut(client: DriveClient, item: dict, result: ScanResult, path: str):
    if item["mimeType"] != SHORTCUT_MIME:
        return item
    target_id = (item.get("shortcutDetails") or {}).get("targetId")
    target_resource_key = (item.get("shortcutDetails") or {}).get(
        "targetResourceKey"
    )
    if not target_id:
        _record_issue(
            result.inaccessible,
            item.get("id"),
            item.get("name", ""),
            f"{path}/{item.get('name', '')}".strip("/"),
            "MissingShortcutTarget",
            "Shortcut has no target ID",
        )
        return None
    try:
        if target_resource_key:
            target = client.get_file(
                target_id, resource_key=target_resource_key
            )
            target["resourceKey"] = target_resource_key
        else:
            target = client.get_file(target_id)
        target["name"] = target.get("name") or item["name"]
        result.resolved_shortcut_ids.append(target_id)
        return target
    except Exception as exc:
        _record_scan_error(
            result,
            exc,
            target_id,
            item.get("name", ""),
            f"{path}/{item.get('name', '')}".strip("/"),
        )
        return None


def _record_scan_error(
    result: ScanResult,
    error: Exception,
    item_id: str,
    name: str,
    path: str,
) -> None:
    if isinstance(error, HttpError) and is_inaccessible_error(error):
        _record_issue(
            result.inaccessible,
            item_id,
            name,
            path,
            f"HTTP{getattr(error.resp, 'status', 'Error')}",
            str(error),
        )
        log.warning("Drive item inaccessible at %s (%s): %s", path, item_id, error)
        return
    if is_transient_drive_error(error):
        _record_issue(
            result.transient_failures,
            item_id,
            name,
            path,
            type(error).__name__,
            str(error),
        )
        log.error("Transient Drive failure at %s (%s): %s", path, item_id, error)
        return
    _record_issue(
        result.errors,
        item_id,
        name,
        path,
        type(error).__name__,
        str(error),
    )
    log.exception("Unexpected Drive scan failure at %s (%s)", path, item_id)


def _record_issue(
    items: list[dict],
    item_id: Optional[str],
    name: str,
    path: str,
    error_type: str,
    reason: str,
) -> None:
    items.append({
        "id": item_id,
        "name": name,
        "path": path,
        "error_type": error_type,
        "reason": reason[:300],
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    })
