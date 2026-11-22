import httplib2
import pytest
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app.drive.client import FOLDER_MIME, SHORTCUT_MIME
from app.drive.folder_scanner import scan_folder


def make_http_error(status, message):
    response = httplib2.Response({"status": str(status)})
    return HttpError(response, message.encode("utf-8"), uri="https://drive.test")


class ShortcutDrive:
    def __init__(self, target_error):
        self.target_error = target_error

    def list_children(self, folder_id):
        yield {
            "id": "shortcut",
            "name": "course-folder",
            "mimeType": SHORTCUT_MIME,
            "shortcutDetails": {"targetId": "target"},
        }
        yield {
            "id": "ordinary-file",
            "name": "notes.pdf",
            "mimeType": "application/pdf",
        }

    def get_file(self, file_id):
        raise self.target_error


@pytest.mark.parametrize(
    "status,message",
    [(403, "permission denied"), (404, "not found")],
)
def test_permanent_shortcut_target_error_is_recorded_and_scan_continues(
    status, message
):
    result = scan_folder(
        ShortcutDrive(make_http_error(status, message)),
        "root",
    )

    assert len(result.inaccessible) == 1
    assert result.inaccessible[0]["id"] == "target"
    assert [file.file_id for file in result.files] == ["ordinary-file"]


@pytest.mark.parametrize(
    "error",
    [
        make_http_error(503, "service unavailable"),
        TransportError("temporary network failure"),
    ],
)
def test_transient_shortcut_target_failure_is_reported_and_scan_continues(error):
    result = scan_folder(ShortcutDrive(error), "root")

    assert result.status == "partial"
    assert len(result.transient_failures) == 1
    assert result.transient_failures[0]["name"] == "course-folder"
    assert result.transient_failures[0]["path"] == "course-folder"
    assert [file.file_id for file in result.files] == ["ordinary-file"]


def test_transient_folder_listing_error_is_reported():
    class UnavailableDrive:
        def list_children(self, folder_id):
            if folder_id == "root":
                yield {
                    "id": "bad-folder",
                    "name": "Shared Folder",
                    "mimeType": "application/vnd.google-apps.folder",
                }
                yield {
                    "id": "file",
                    "name": "notes.pdf",
                    "mimeType": "application/pdf",
                }
                return
            raise make_http_error(503, "service unavailable")

    result = scan_folder(UnavailableDrive(), "root")

    assert result.status == "partial"
    assert result.transient_failures[0]["id"] == "bad-folder"
    assert [file.file_id for file in result.files] == ["file"]


def test_resource_keys_propagate_through_file_and_nested_folder_shortcuts():
    class ResourceKeyDrive:
        def __init__(self):
            self.list_calls = []
            self.get_calls = []

        def list_children(self, folder_id, resource_key=None):
            self.list_calls.append((folder_id, resource_key))
            if folder_id == "root":
                yield {
                    "id": "file-shortcut",
                    "name": "direct.pdf",
                    "mimeType": SHORTCUT_MIME,
                    "shortcutDetails": {
                        "targetId": "direct-file",
                        "targetMimeType": "application/pdf",
                        "targetResourceKey": "direct-key",
                    },
                }
                yield {
                    "id": "folder-shortcut",
                    "name": "course",
                    "mimeType": SHORTCUT_MIME,
                    "shortcutDetails": {
                        "targetId": "folder-one",
                        "targetMimeType": FOLDER_MIME,
                        "targetResourceKey": "folder-one-key",
                    },
                }
            elif folder_id == "folder-one":
                yield {
                    "id": "nested-folder-shortcut",
                    "name": "materials",
                    "mimeType": SHORTCUT_MIME,
                    "shortcutDetails": {
                        "targetId": "folder-two",
                        "targetMimeType": FOLDER_MIME,
                        "targetResourceKey": "folder-two-key",
                    },
                }
            elif folder_id == "folder-two":
                yield {
                    "id": "nested-file-shortcut",
                    "name": "nested.pdf",
                    "mimeType": SHORTCUT_MIME,
                    "shortcutDetails": {
                        "targetId": "nested-file",
                        "targetMimeType": "application/pdf",
                        "targetResourceKey": "nested-file-key",
                    },
                }

        def get_file(self, file_id, resource_key=None):
            self.get_calls.append((file_id, resource_key))
            if file_id.startswith("folder-"):
                return {
                    "id": file_id,
                    "name": file_id,
                    "mimeType": FOLDER_MIME,
                }
            return {
                "id": file_id,
                "name": file_id + ".pdf",
                "mimeType": "application/pdf",
            }

    drive = ResourceKeyDrive()
    result = scan_folder(drive, "root")

    assert result.status == "complete"
    assert {file.file_id for file in result.files} == {
        "direct-file",
        "nested-file",
    }
    assert drive.get_calls == [
        ("direct-file", "direct-key"),
        ("folder-one", "folder-one-key"),
        ("folder-two", "folder-two-key"),
        ("nested-file", "nested-file-key"),
    ]
    assert drive.list_calls == [
        ("root", None),
        ("folder-one", "folder-one-key"),
        ("folder-two", "folder-two-key"),
    ]
