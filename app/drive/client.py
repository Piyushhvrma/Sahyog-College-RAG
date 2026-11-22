"""Thin wrapper over the Drive v3 API: listing (paginated), metadata, download/export."""
import io
import logging
import socket
import time
from typing import Iterator

import httplib2
from google.auth.exceptions import TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app.config.settings import Settings, get_settings
from app.drive.auth import get_credentials

log = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GSLIDES = "application/vnd.google-apps.presentation"

# Google-native type -> (export mime, extension)
EXPORT_MAP = {
    GDOC: ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    GSHEET: ("text/csv", ".csv"),  # NOTE: Drive CSV export returns only the first sheet
    GSLIDES: ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}

FILE_FIELDS = ("id, name, mimeType, size, md5Checksum, modifiedTime, webViewLink, "
               "parents, resourceKey, "
               "shortcutDetails(targetId, targetMimeType, targetResourceKey), trashed")
RESOURCE_KEYS_HEADER = "X-Goog-Drive-Resource-Keys"
DRIVE_API_TIMEOUT_SECONDS = 30
DRIVE_API_MAX_ATTEMPTS = 5
DRIVE_API_MAX_BACKOFF_SECONDS = 8
_TRANSPORT_ERRORS = (
    httplib2.HttpLib2Error,
    TransportError,
    OSError,
    socket.timeout,
)


class DriveFileTooLarge(Exception):
    pass


class DriveClient:
    def __init__(
        self,
        settings: Settings | None = None,
        service=None,
        interactive_auth: bool | None = None,
    ):
        self.settings = settings or get_settings()
        self._service = service
        self.interactive_auth = (
            self.settings.drive_interactive_auth
            if interactive_auth is None
            else interactive_auth
        )

    @property
    def service(self):
        if self._service is None:
            creds = get_credentials(
                self.settings,
                interactive=self.interactive_auth,
            )
            http = AuthorizedHttp(
                creds,
                http=httplib2.Http(timeout=DRIVE_API_TIMEOUT_SECONDS),
            )
            self._service = build(
                "drive",
                "v3",
                http=http,
                cache_discovery=False,
            )
        return self._service

    def _execute(self, request):
        """Execute with bounded retries for transient Drive API failures."""
        for attempt in range(DRIVE_API_MAX_ATTEMPTS):
            try:
                return request.execute(num_retries=0)
            except HttpError as exc:
                status = getattr(exc.resp, "status", 0)
                retryable = status in (429, 500, 502, 503, 504) or (
                    status == 403 and "rateLimit" in str(exc)
                )
                if (
                    not retryable
                    or attempt == DRIVE_API_MAX_ATTEMPTS - 1
                ):
                    raise
                _wait_before_retry(attempt, status)
            except _TRANSPORT_ERRORS as exc:
                if attempt == DRIVE_API_MAX_ATTEMPTS - 1:
                    raise
                _wait_before_retry(attempt, type(exc).__name__)

        raise RuntimeError("Drive request retry loop exited unexpectedly")

    def about(self) -> dict:
        return self._execute(self.service.about().get(fields="user(displayName,emailAddress)"))

    def get_file(self, file_id: str, resource_key: str | None = None) -> dict:
        request = self.service.files().get(
            fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True)
        return self._execute(
            _with_resource_key(request, file_id, resource_key)
        )

    def list_children(
        self, folder_id: str, resource_key: str | None = None
    ) -> Iterator[dict]:
        """Yield every non-trashed child of a folder, following pagination."""
        page_token = None
        while True:
            request = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=f"nextPageToken, files({FILE_FIELDS})",
                pageSize=200,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                orderBy="name",
            )
            resp = self._execute(
                _with_resource_key(request, folder_id, resource_key)
            )
            yield from resp.get("files", [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def download(self, file: dict, dest) -> str:
        """Download/export `file` into `dest` (Path). Returns the extension used.
        Streams to disk in chunks, so large files are never fully held in memory."""
        mime = file["mimeType"]
        limit = self.settings.max_download_mb * 1024 * 1024
        size = int(file.get("size") or 0)
        if size and size > limit:
            raise DriveFileTooLarge(f"{file['name']} is {size/1048576:.1f} MB > MAX_DOWNLOAD_MB")

        if mime in EXPORT_MAP:
            export_mime, ext = EXPORT_MAP[mime]
            request = self.service.files().export_media(fileId=file["id"], mimeType=export_mime)
        else:
            ext = ""
            request = self.service.files().get_media(fileId=file["id"], supportsAllDrives=True)
        request = _with_resource_key(request, file["id"], file.get("resourceKey"))

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if fh.tell() > limit:
                    raise DriveFileTooLarge(f"{file['name']} exceeded MAX_DOWNLOAD_MB while downloading")
        return ext


def _with_resource_key(request, file_id: str, resource_key: str | None):
    if resource_key:
        request.headers[RESOURCE_KEYS_HEADER] = f"{file_id}/{resource_key}"
    return request


def is_inaccessible_error(exc: HttpError) -> bool:
    """Return whether Drive explicitly denied access or reports a missing resource."""
    status = getattr(exc.resp, "status", 0)
    if status == 404:
        return True
    return status == 403 and "rateLimit" not in str(exc)


def is_transient_drive_error(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        return status in (429, 500, 502, 503, 504) or (
            status == 403 and "rateLimit" in str(exc)
        )
    return isinstance(exc, _TRANSPORT_ERRORS)


def _wait_before_retry(attempt: int, reason) -> None:
    delay = min(2 ** attempt, DRIVE_API_MAX_BACKOFF_SECONDS)
    log.warning("Drive API request failed (%s); retry in %ss", reason, delay)
    time.sleep(delay)
