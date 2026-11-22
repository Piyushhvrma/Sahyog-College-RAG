"""Google Drive OAuth (Desktop client, read-only). First run opens a browser; the refresh token is saved and reused."""
import logging
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config.settings import Settings, get_settings

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class DriveAuthError(RuntimeError):
    pass


def _save_token(creds: Credentials, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # best-effort; no-op semantics on Windows
    except OSError:
        pass


def get_credentials(settings: Settings | None = None, interactive: bool = True) -> Credentials:
    """Load token -> refresh if expired -> fall back to browser flow if refresh fails."""
    s = settings or get_settings()
    creds: Credentials | None = None

    if s.token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(s.token_file), SCOPES)
        except (ValueError, KeyError) as exc:
            log.warning("Token file unreadable (%s); re-authorizing.", exc)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, s.token_file)
            return creds
        except RefreshError as exc:
            log.warning("Refresh token rejected (%s). Re-authorization required.", exc)
            creds = None

    if not interactive:
        raise DriveAuthError("Drive token missing/expired. Run: python -m scripts.test_drive")
    if not s.credentials_file.exists():
        raise DriveAuthError(
            f"OAuth client file not found: {s.credentials_file}. "
            "See docs/GOOGLE_DRIVE_SETUP.md."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(s.credentials_file), SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh_token is issued.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    _save_token(creds, s.token_file)
    log.info("Drive authorization saved to %s", s.token_file)
    return creds
