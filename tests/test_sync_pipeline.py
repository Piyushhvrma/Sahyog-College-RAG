"""End-to-end ingestion with a fake Drive, fake embedder and mongomock (no network, no credentials)."""
import fitz
import httplib2
import mongomock
import pytest
from googleapiclient.errors import HttpError

from app.config.settings import get_settings
from app.drive.client import GDOC, SHORTCUT_MIME
from app.drive.sync import (
    SyncAlreadyRunningError,
    _sync_lease,
    run_sync,
)
from app.vectorstore.mongo_client import (
    CHUNKS,
    DOCUMENTS,
    INACCESSIBLE_ITEMS,
)


class FakeEmbedder:
    model, dimensions = "gemini-embedding-2", 768
    meta = {"embedding_model": model, "embedding_dimensions": dimensions}

    def embed_documents(self, texts, titles=None):
        return [[0.01] * 768 for _ in texts]


def make_pdf(path, text):
    d = fitz.open()
    d.new_page().insert_text((72, 72), text)
    d.save(path)


class FakeDrive:
    def __init__(self, tmp):
        self.tmp, self.tree, self.contents = tmp, {}, {}

    def add(self, folder, fid, name, mime, text, md5="m1"):
        self.tree.setdefault(folder, []).append({"id": fid, "name": name, "mimeType": mime, "md5Checksum": md5,
                                                 "modifiedTime": "t", "webViewLink": f"https://drive/{fid}"})
        self.contents[fid] = text

    def add_folder(self, parent, fid, name):
        self.tree.setdefault(parent, []).append({"id": fid, "name": name,
                                                 "mimeType": "application/vnd.google-apps.folder"})

    def list_children(self, fid):
        yield from self.tree.get(fid, [])

    def get_file(self, fid):
        raise KeyError(fid)

    def download(self, file, dest):
        make_pdf(dest, self.contents[file["id"]]) if file["name"].endswith(".pdf") else dest.write_text("x")
        return ".pdf"


def make_http_error(status, message):
    response = httplib2.Response({"status": str(status)})
    return HttpError(response, message.encode("utf-8"), uri="https://drive.test")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_ROOT_FOLDER_ID", "root")
    get_settings.cache_clear()
    drive = FakeDrive(tmp_path)
    drive.add_folder("root", "f1", "CSE")
    drive.add_folder("f1", "f2", "Semester 5")
    drive.add("f2", "A", "os notes.pdf", "application/pdf", "Operating systems deadlock " * 20)
    drive.add("f2", "B", "dup.pdf", "application/pdf", "Operating systems deadlock " * 20)
    drive.add("f2", "C", "broken.pdf", "application/pdf", "")       # empty -> extraction failure
    return drive, mongomock.MongoClient()["t"], FakeEmbedder()


def test_full_flow_incremental_failures_duplicates_deletion(setup):
    drive, db, emb = setup
    s = run_sync(db, drive, emb, progress=lambda m: None)
    assert s.new == 1 and s.duplicates == 1 and s.failed == 1 and s.scanned == 3
    a = db[DOCUMENTS].find_one({"file_id": "A"})
    assert a["status"] == "indexed" and a["branch"] == "CSE" and a["semester"] == 5 and a["approved"] is False
    assert db[DOCUMENTS].find_one({"file_id": "B"})["status"] == "duplicate"
    assert db[DOCUMENTS].find_one({"file_id": "C"})["status"] == "failed"
    ch = db[CHUNKS].find_one({"document_id": "A"})
    assert (ch["page_number"] == 1 and ch["source_url"] == "https://drive/A"
            and len(ch["embedding"]) == 768 and ch["approved"] is False)

    # unchanged -> skipped, nothing re-embedded
    s2 = run_sync(db, drive, emb, progress=lambda m: None)
    assert s2.skipped_unchanged == 2 and s2.new == 0 and s2.updated == 0

    # modified file -> reprocessed, old chunks replaced, approval preserved
    db[DOCUMENTS].update_one({"file_id": "A"}, {"$set": {"approved": True}})
    db[CHUNKS].update_many({"document_id": "A"}, {"$set": {"approved": True}})
    drive.tree["f2"][0]["md5Checksum"] = "m2"
    drive.contents["A"] = "Totally different content about placements " * 20
    s3 = run_sync(db, drive, emb, progress=lambda m: None)
    assert s3.updated == 1
    assert db[CHUNKS].count_documents({"document_id": "A"}) == 1
    assert "placements" in db[CHUNKS].find_one({"document_id": "A"})["text"]
    assert db[DOCUMENTS].find_one({"file_id": "A"})["approved"] is True
    assert db[CHUNKS].find_one({"document_id": "A"})["approved"] is True

    # file removed from Drive -> deleted + chunks gone
    drive.tree["f2"] = [x for x in drive.tree["f2"] if x["id"] != "A"]
    s4 = run_sync(db, drive, emb, progress=lambda m: None)
    assert s4.deleted == 1 and db[CHUNKS].count_documents({"document_id": "A"}) == 0


def test_model_change_triggers_reprocess(setup):
    drive, db, emb = setup
    run_sync(db, drive, emb, progress=lambda m: None)

    class Other(FakeEmbedder):
        model = "other-model"
        meta = {"embedding_model": "other-model", "embedding_dimensions": 768}
    s = run_sync(db, drive, Other(), progress=lambda m: None)
    assert s.updated >= 1


def test_dry_run_writes_nothing(setup):
    drive, db, emb = setup
    run_sync(db, drive, emb, dry_run=True, progress=lambda m: None)
    assert db[DOCUMENTS].count_documents({}) == 0 and db[CHUNKS].count_documents({}) == 0


def test_corrupt_pdf_failure_does_not_abort_drive_sync_or_temp_cleanup(setup):
    drive, db, emb = setup
    drive.add("f2", "D", "corrupt.pdf", "application/pdf", "unused")
    download = drive.download

    def download_with_corrupt_pdf(file, dest):
        if file["id"] == "D":
            dest.write_bytes(b"not a PDF")
            return ".pdf"
        return download(file, dest)

    drive.download = download_with_corrupt_pdf
    summary = run_sync(db, drive, emb, progress=lambda _: None)

    assert summary.failed == 2
    assert db[DOCUMENTS].find_one({"file_id": "D"})["status"] == "failed"
    assert db[DOCUMENTS].find_one({"file_id": "A"})["status"] == "indexed"


def test_partial_scan_never_deletes_existing_indexed_files(setup):
    drive, db, emb = setup
    run_sync(db, drive, emb, progress=lambda m: None)
    drive.tree["f2"] = [
        item for item in drive.tree["f2"] if item["id"] != "A"
    ]
    drive.add_folder("root", "inaccessible-subtree", "Shared Folder")
    list_children = drive.list_children

    def list_with_transient_failure(folder_id):
        if folder_id == "inaccessible-subtree":
            raise make_http_error(503, "service unavailable")
        yield from list_children(folder_id)

    drive.list_children = list_with_transient_failure
    summary = run_sync(db, drive, emb, progress=lambda m: None)

    assert summary.scan_status == "partial"
    assert summary.transient_failures == 1
    assert summary.deleted == 0
    assert db[DOCUMENTS].find_one({"file_id": "A"})["status"] == "indexed"
    assert db[CHUNKS].count_documents({"document_id": "A"}) > 0


def test_inaccessible_shortcut_is_recorded_and_retried(setup):
    drive, db, emb = setup
    drive.tree.setdefault("root", []).append({
        "id": "shortcut",
        "name": "new notes.pdf",
        "mimeType": SHORTCUT_MIME,
        "shortcutDetails": {"targetId": "target-file"},
    })
    drive.get_file = lambda _file_id: (_ for _ in ()).throw(
        make_http_error(404, "not found")
    )

    first = run_sync(db, drive, emb, progress=lambda m: None)
    saved = db[INACCESSIBLE_ITEMS].find_one({"id": "target-file"})
    assert first.scan_status == "partial"
    assert saved["name"] == "new notes.pdf"
    assert saved["path"] == "new notes.pdf"
    assert saved["error_type"] == "HTTP404"
    assert saved["last_checked_at"]

    drive.contents["target-file"] = "Recovered shared notes " * 20
    drive.get_file = lambda file_id: {
        "id": file_id,
        "name": "new notes.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "t2",
        "md5Checksum": "m2",
        "webViewLink": "https://drive/target-file",
    }

    second = run_sync(db, drive, emb, progress=lambda m: None)
    assert second.scan_status == "complete"
    assert db[DOCUMENTS].find_one({"file_id": "target-file"})["status"] == "indexed"
    assert db[INACCESSIBLE_ITEMS].count_documents({"id": "target-file"}) == 0


def test_sync_lease_rejects_overlapping_runs():
    db = mongomock.MongoClient()["sync-lock"]

    with _sync_lease(db):
        with pytest.raises(SyncAlreadyRunningError):
            with _sync_lease(db):
                pass
