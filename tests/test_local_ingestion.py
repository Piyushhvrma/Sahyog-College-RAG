import io
import mongomock
import pytest
import stat
import zipfile

from app.config.settings import PROJECT_ROOT, Settings
from app.ingestion import local_source
from app.ingestion.local_source import (
    LocalFileClient,
    LocalKnowledgeFolderError,
    scan_local_folder,
)
from app.ingestion.local_sync import run_local_ingestion
from app.vectorstore.mongo_client import CHUNKS, DOCUMENTS, SYNC_LOGS


class FakeEmbedder:
    model = "gemini-embedding-2"
    dimensions = 768
    meta = {"embedding_model": model, "embedding_dimensions": dimensions}

    def __init__(self):
        self.calls = []

    def embed_documents(self, texts, titles=None):
        self.calls.append(list(texts))
        return [[0.01] * self.dimensions for _ in texts]


class NoEmbeddingAllowed:
    model = "gemini-embedding-2"
    dimensions = 768
    meta = {"embedding_model": model, "embedding_dimensions": dimensions}

    def embed_documents(self, *args, **kwargs):
        raise AssertionError("dry-run must not request embeddings")


def make_pdf(path, text):
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    document.save(path)
    document.close()


def make_settings(root, **kwargs):
    kwargs.setdefault("auto_approve", False)
    return Settings(
        _env_file=None,
        local_knowledge_dir=str(root),
        ocr_enabled=False,
        **kwargs,
    )


def test_local_scanner_preserves_path_and_reports_types_duplicates_and_limits(
    tmp_path,
):
    root = tmp_path / "materials"
    nested = root / "2ND YEAR" / "CSE" / "3RD SEMESTER" / "OS"
    nested.mkdir(parents=True)
    (nested / "notes.txt").write_text("Operating systems notes", encoding="utf-8")
    (nested / "notes-copy.txt").write_bytes(
        (nested / "notes.txt").read_bytes()
    )
    (nested / "readme.bin").write_bytes(b"not supported")
    oversized = nested / "large.txt"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))

    result = scan_local_folder(root, make_settings(root, max_download_mb=1))

    assert result.status == "partial"
    assert len(result.files) == 2
    assert result.files[0].folder_path == "2ND YEAR/CSE/3RD SEMESTER/OS"
    assert result.files[0].raw["source_path"].startswith("2ND YEAR/CSE/")
    assert len(result.duplicates) == 1
    assert result.unsupported[0]["path"].endswith("readme.bin")
    assert len(result.oversized) == 1
    assert result.inaccessible == []


def test_scanner_indexes_nested_zip_members_without_extracting_or_overwriting(
    tmp_path,
):
    root = tmp_path / "materials"
    root.mkdir()
    inner_bytes = io.BytesIO()
    with zipfile.ZipFile(inner_bytes, "w") as inner:
        inner.writestr("courses/DBMS 2024 PYQ.txt", "Database systems PYQ " * 20)
    outer_path = root / "course-pack.zip"
    with zipfile.ZipFile(outer_path, "w") as outer:
        outer.writestr("notes/OS Unit 1.txt", "Operating systems unit one " * 20)
        outer.writestr("nested.zip", inner_bytes.getvalue())
        outer.writestr("../outside.txt", "must never be written")
    archive_before = outer_path.read_bytes()
    outside_path = tmp_path / "outside.txt"

    scan = scan_local_folder(root, make_settings(root))

    assert scan.status == "partial"
    assert scan.archives == 2
    assert scan.archive_files == 2
    assert len(scan.files) == 2
    assert scan.files[0].raw["source_path"].startswith("course-pack.zip!/")
    assert any("Unsafe ZIP member path" in item["reason"] for item in scan.errors)
    assert not outside_path.exists()

    extracted = tmp_path / "nested-output.txt"
    LocalFileClient(make_settings(root)).download(scan.files[1].raw, extracted)
    assert extracted.read_text(encoding="utf-8").startswith("Database systems PYQ")
    assert outer_path.read_bytes() == archive_before


def test_ingestion_reuses_pipeline_for_a_zip_member_and_preserves_citation_path(
    tmp_path,
):
    root = tmp_path / "materials"
    root.mkdir()
    pdf_path = tmp_path / "source.pdf"
    make_pdf(pdf_path, "Operating systems scheduling and deadlocks " * 20)
    with zipfile.ZipFile(root / "notes.zip", "w") as archive:
        archive.write(pdf_path, "Operating Systems/OS Unit 1.pdf")
    db = mongomock.MongoClient()["local-zip-ingestion"]

    summary = run_local_ingestion(
        db, root, FakeEmbedder(), settings=make_settings(root), progress=lambda _: None
    )

    document = db[DOCUMENTS].find_one({"source_type": "local"})
    chunk = db[CHUNKS].find_one({"document_id": document["file_id"]})
    assert summary.new == 1
    assert summary.archive_files == 1
    assert document["source_path"] == "notes.zip!/Operating Systems/OS Unit 1.pdf"
    assert document["unit"] == "1"
    assert document["subject"] == "Operating Systems"
    assert chunk["source_path"] == document["source_path"]
    assert chunk["page_number"] == 1


def test_corrupt_and_encrypted_zip_entries_are_reported_and_scan_continues(
    tmp_path,
):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "valid.txt").write_text("Valid academic notes " * 20, encoding="utf-8")
    (root / "broken.zip").write_bytes(b"not a zip file")
    encrypted_path = root / "password.zip"
    with zipfile.ZipFile(encrypted_path, "w") as archive:
        archive.writestr("locked.txt", "private academic notes")
    content = bytearray(encrypted_path.read_bytes())
    central = content.find(b"PK\x01\x02")
    assert central >= 0
    content[central + 8] |= 0x01
    encrypted_path.write_bytes(content)

    scan = scan_local_folder(root, make_settings(root))

    assert scan.status == "partial"
    assert [file.name for file in scan.files] == ["valid.txt"]
    assert any(item["path"] == "broken.zip" for item in scan.errors)
    assert any("Encrypted/password-protected" in item["reason"] for item in scan.inaccessible)


def test_zip_member_size_and_duplicate_names_are_bounded(tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    archive_path = root / "limited.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("large.txt", "x" * (1024 * 1024 + 1))
        archive.writestr("same.txt", "one")
        with pytest.warns(UserWarning):
            archive.writestr("same.txt", "two")

    scan = scan_local_folder(
        root, make_settings(root, max_download_mb=1)
    )

    assert scan.status == "partial"
    assert not any(file.name == "large.txt" for file in scan.files)
    assert any(item["path"].endswith("large.txt") for item in scan.oversized)
    assert any("Duplicate ZIP member path" in item["reason"] for item in scan.errors)


def test_local_ingestion_is_incremental_preserves_approval_and_never_deletes_missing(
    tmp_path,
):
    root = tmp_path / "materials"
    file_path = (
        root / "2ND YEAR" / "CSE" / "3RD SEMESTER" / "Operating Systems"
        / "PYQ" / "2023-24" / "CS-301 End Sem.pdf"
    )
    make_pdf(file_path, "Operating systems scheduling and deadlocks " * 20)
    settings = make_settings(root, auto_approve=True)
    db = mongomock.MongoClient()["local-ingestion"]
    embedder = FakeEmbedder()

    first = run_local_ingestion(
        db, root, embedder, settings=settings, progress=lambda _: None
    )
    document = db[DOCUMENTS].find_one({"source_type": "local"})
    assert first.new == 1 and first.failed == 0
    assert document["approved"] is False
    assert document["source_path"].endswith("CS-301 End Sem.pdf")
    assert document["study_year"] == 2
    assert document["branch"] == "CSE"
    assert document["semester"] == 3
    assert document["subject"] == "Operating Systems"
    assert document["academic_year"] == "2023-24"
    assert db[CHUNKS].find_one({"document_id": document["file_id"]})[
        "source_path"
    ] == document["source_path"]

    second = run_local_ingestion(
        db, root, embedder, settings=settings, progress=lambda _: None
    )
    assert second.skipped_unchanged == 1
    assert len(embedder.calls) == 1

    db[DOCUMENTS].update_one(
        {"file_id": document["file_id"]}, {"$set": {"approved": True}}
    )
    db[CHUNKS].update_many(
        {"document_id": document["file_id"]}, {"$set": {"approved": True}}
    )
    make_pdf(file_path, "Updated course material about paging " * 20)
    updated = run_local_ingestion(
        db, root, embedder, settings=settings, progress=lambda _: None
    )
    assert updated.updated == 1
    assert db[DOCUMENTS].find_one({"file_id": document["file_id"]})["approved"] is True
    assert db[CHUNKS].find_one({"document_id": document["file_id"]})["approved"] is True

    file_path.unlink()
    absent = run_local_ingestion(
        db, root, embedder, settings=settings, progress=lambda _: None
    )
    assert absent.scanned == 0
    assert db[DOCUMENTS].find_one({"file_id": document["file_id"]})["status"] == "indexed"
    assert db[CHUNKS].count_documents({"document_id": document["file_id"]}) > 0


def test_local_dry_run_does_not_write_or_embed(tmp_path):
    root = tmp_path / "materials"
    (root / "CSE").mkdir(parents=True)
    (root / "CSE" / "notes.txt").write_text(
        "Operating systems course notes " * 10, encoding="utf-8"
    )
    db = mongomock.MongoClient()["local-dry-run"]

    summary = run_local_ingestion(
        db,
        root,
        NoEmbeddingAllowed(),
        settings=make_settings(root),
        dry_run=True,
        progress=lambda _: None,
    )

    assert summary.new == 1
    assert summary.processed_count == 1
    assert db.list_collection_names() == []


def test_local_byte_duplicates_are_not_embedded_twice(tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    contents = "Same operating systems study notes. " * 20
    (root / "first.txt").write_text(contents, encoding="utf-8")
    (root / "second.txt").write_text(contents, encoding="utf-8")
    db = mongomock.MongoClient()["local-duplicates"]
    embedder = FakeEmbedder()

    summary = run_local_ingestion(
        db, root, embedder, settings=make_settings(root), progress=lambda _: None
    )

    assert summary.new == 1
    assert summary.duplicates == 1
    assert len(embedder.calls) == 1
    assert db[DOCUMENTS].count_documents({"source_type": "local"}) == 2
    assert db[DOCUMENTS].count_documents({"status": "duplicate"}) == 1


def test_extracted_text_headings_are_saved_as_document_and_chunk_metadata(tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "material.txt").write_text(
        "Subject: Computer Networks\n"
        "Unit 2: Routing and forwarding\n"
        "A computer networks course note. " * 20,
        encoding="utf-8",
    )
    db = mongomock.MongoClient()["local-text-metadata"]

    summary = run_local_ingestion(
        db, root, FakeEmbedder(), settings=make_settings(root), progress=lambda _: None
    )

    document = db[DOCUMENTS].find_one({"source_type": "local"})
    chunk = db[CHUNKS].find_one({"document_id": document["file_id"]})
    assert summary.failed == 0
    assert document["subject"] == "Computer Networks"
    assert document["unit"] == "2"
    assert chunk["subject"] == "Computer Networks"
    assert chunk["unit"] == "2"


def test_corrupt_local_file_is_tracked_and_other_files_continue(tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "broken.pdf").write_bytes(b"not a PDF")
    (root / "notes.txt").write_text("A readable course document " * 10, encoding="utf-8")
    db = mongomock.MongoClient()["local-failures"]

    summary = run_local_ingestion(
        db, root, FakeEmbedder(), settings=make_settings(root), progress=lambda _: None
    )

    failed = db[DOCUMENTS].find_one({"file_name": "broken.pdf"})
    assert summary.failed == 1
    assert failed["status"] == "failed"
    assert failed["source_type"] == "local"
    assert failed["error"]
    assert db[DOCUMENTS].find_one({"file_name": "notes.txt"})["status"] == "indexed"
    assert db[SYNC_LOGS].find_one({"source": "local"})["summary"]["failed"] == 1


def test_scanner_rejects_project_root_without_walking_it():
    with pytest.raises(LocalKnowledgeFolderError, match="project root"):
        scan_local_folder(
            PROJECT_ROOT,
            Settings(_env_file=None),
        )


def test_local_scanner_recognizes_every_existing_local_loader_format(tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    for filename in ("notes.pdf", "notes.docx", "slides.pptx", "text.txt", "table.csv"):
        (root / filename).write_bytes(b"test bytes")

    scan = scan_local_folder(root, make_settings(root))

    assert {file.kind for file in scan.files} == {
        "pdf",
        "docx",
        "pptx",
        "txt",
        "csv",
    }
    assert scan.unsupported == []


def test_unreadable_local_file_is_reported_as_partial(tmp_path, monkeypatch):
    root = tmp_path / "materials"
    root.mkdir()
    (root / "notes.txt").write_text("course notes", encoding="utf-8")

    def deny_read(_path):
        raise PermissionError("test access denied")

    monkeypatch.setattr(local_source, "_hash_file", deny_read)
    scan = scan_local_folder(root, make_settings(root))

    assert scan.status == "partial"
    assert scan.files == []
    assert scan.inaccessible[0]["path"] == "notes.txt"
    assert "PermissionError" in scan.inaccessible[0]["reason"]
