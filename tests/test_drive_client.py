import httplib2
import pytest
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app.drive import client as drive_client
from app.drive.client import (
    DRIVE_API_MAX_ATTEMPTS,
    DRIVE_API_MAX_BACKOFF_SECONDS,
    DRIVE_API_TIMEOUT_SECONDS,
    DriveClient,
    FILE_FIELDS,
    RESOURCE_KEYS_HEADER,
)


def make_http_error(status, message="temporary"):
    response = httplib2.Response({"status": str(status)})
    return HttpError(response, message.encode("utf-8"), uri="https://drive.test")


class FakeRequest:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.headers = {}

    def execute(self, num_retries):
        self.calls.append(num_retries)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_drive_service_uses_finite_socket_timeout(monkeypatch):
    credentials = object()
    captured = {}
    service = object()

    monkeypatch.setattr(
        drive_client,
        "get_credentials",
        lambda settings, interactive: credentials,
    )
    monkeypatch.setattr(
        drive_client,
        "build",
        lambda *args, **kwargs: captured.update(kwargs) or service,
    )

    assert DriveClient().service is service
    assert captured["http"].http.timeout == DRIVE_API_TIMEOUT_SECONDS
    assert "credentials" not in captured


def test_execute_retries_transient_http_errors_with_capped_backoff(monkeypatch):
    request = FakeRequest(
        [
            make_http_error(429),
            make_http_error(500),
            make_http_error(502),
            make_http_error(503),
            {"ok": True},
        ]
    )
    delays = []
    monkeypatch.setattr(drive_client.time, "sleep", delays.append)

    assert DriveClient(service=object())._execute(request) == {"ok": True}
    assert len(request.calls) == DRIVE_API_MAX_ATTEMPTS
    assert request.calls == [0] * DRIVE_API_MAX_ATTEMPTS
    assert delays == [
        min(2**attempt, DRIVE_API_MAX_BACKOFF_SECONDS)
        for attempt in range(DRIVE_API_MAX_ATTEMPTS - 1)
    ]


@pytest.mark.parametrize("status", [403, 404])
def test_execute_does_not_retry_permanent_http_errors(monkeypatch, status):
    error = make_http_error(status, "permission denied")
    request = FakeRequest([error])
    delays = []
    monkeypatch.setattr(drive_client.time, "sleep", delays.append)

    with pytest.raises(HttpError):
        DriveClient(service=object())._execute(request)

    assert len(request.calls) == 1
    assert delays == []


def test_execute_retries_temporary_transport_errors_and_stops_at_limit(monkeypatch):
    request = FakeRequest(
        [TransportError("temporary network failure")]
        * DRIVE_API_MAX_ATTEMPTS
    )
    delays = []
    monkeypatch.setattr(drive_client.time, "sleep", delays.append)

    with pytest.raises(TransportError):
        DriveClient(service=object())._execute(request)

    assert len(request.calls) == DRIVE_API_MAX_ATTEMPTS
    assert delays == [
        min(2**attempt, DRIVE_API_MAX_BACKOFF_SECONDS)
        for attempt in range(DRIVE_API_MAX_ATTEMPTS - 1)
    ]


class FakeFilesApi:
    def __init__(self):
        self.get_calls = []
        self.get_requests = []
        self.list_calls = []
        self.media_calls = []
        self.list_responses = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        request = FakeRequest([{"id": kwargs["fileId"]}])
        self.get_requests.append(request)
        return request

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.list_responses.pop(0)

    def get_media(self, **kwargs):
        self.media_calls.append(kwargs)
        return FakeRequest([None])


class FakeDriveService:
    def __init__(self):
        self.files_api = FakeFilesApi()

    def files(self):
        return self.files_api


def test_shortcut_metadata_fields_include_resource_keys():
    assert "resourceKey" in FILE_FIELDS
    assert (
        "shortcutDetails(targetId, targetMimeType, targetResourceKey)"
        in FILE_FIELDS
    )


def test_get_file_applies_resource_key_header_only_when_provided():
    service = FakeDriveService()
    client = DriveClient(service=service)

    client.get_file("target-with-key", resource_key="secret-key")
    client.get_file("target-without-key")

    keyed, unkeyed = service.files_api.get_calls
    assert keyed["fields"] == FILE_FIELDS
    assert "resourceKey" not in keyed
    keyed_request, unkeyed_request = service.files_api.get_requests
    assert (
        keyed_request.headers[RESOURCE_KEYS_HEADER]
        == "target-with-key/secret-key"
    )
    assert RESOURCE_KEYS_HEADER not in unkeyed_request.headers


def test_list_children_applies_folder_resource_key_across_pages():
    service = FakeDriveService()
    first = FakeRequest([{
        "files": [{"id": "first"}],
        "nextPageToken": "next",
    }])
    second = FakeRequest([{"files": [{"id": "second"}]}])
    service.files_api.list_responses = [first, second]

    children = list(
        DriveClient(service=service).list_children(
            "shared-folder", resource_key="folder-key"
        )
    )

    assert [item["id"] for item in children] == ["first", "second"]
    assert len(service.files_api.list_calls) == 2
    assert service.files_api.list_calls[0]["pageToken"] is None
    assert service.files_api.list_calls[1]["pageToken"] == "next"
    assert first.headers[RESOURCE_KEYS_HEADER] == "shared-folder/folder-key"
    assert second.headers[RESOURCE_KEYS_HEADER] == "shared-folder/folder-key"


def test_download_applies_file_resource_key_header(monkeypatch, tmp_path):
    service = FakeDriveService()
    captured = {}

    class FakeDownloader:
        def __init__(self, _file_handle, request, chunksize):
            captured["request"] = request
            captured["chunksize"] = chunksize

        def next_chunk(self):
            return None, True

    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", FakeDownloader)
    DriveClient(service=service).download(
        {
            "id": "shared-file",
            "name": "notes.pdf",
            "mimeType": "application/pdf",
            "resourceKey": "file-key",
        },
        tmp_path / "notes.pdf",
    )

    assert service.files_api.media_calls == [
        {"fileId": "shared-file", "supportsAllDrives": True}
    ]
    assert (
        captured["request"].headers[RESOURCE_KEYS_HEADER]
        == "shared-file/file-key"
    )
