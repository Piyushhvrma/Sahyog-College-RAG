"""Read-only recursive scanner and file adapter for local knowledge folders."""
import hashlib
import io
import logging
import os
import stat
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from app.config.settings import PROJECT_ROOT, Settings
from app.drive.folder_scanner import DriveFile

log = logging.getLogger(__name__)

SUPPORTED_LOCAL_TYPES = {
    ".pdf": ("application/pdf", "pdf"),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
    ".txt": ("text/plain", "txt"),
    ".csv": ("text/csv", "csv"),
}
HASH_BLOCK_SIZE = 1024 * 1024
MAX_ARCHIVE_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_DEPTH = 3
MAX_NESTED_ARCHIVE_BYTES = 50 * 1024 * 1024


class _ArchiveLimitError(ValueError):
    pass


class LocalKnowledgeFolderError(ValueError):
    pass


class LocalFileChangedError(OSError):
    pass


@dataclass
class LocalScanResult:
    root: Path
    files: list[DriveFile] = field(default_factory=list)
    archives: int = 0
    archive_files: int = 0
    unsupported: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    inaccessible: list[dict] = field(default_factory=list)
    oversized: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = None
    status: str = "running"


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _issue(path: str, reason: str) -> dict:
    return {"path": path, "reason": reason[:300]}


def _validate_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    project_root = PROJECT_ROOT.resolve()
    if root == project_root or project_root.is_relative_to(root):
        raise LocalKnowledgeFolderError(
            "LOCAL_KNOWLEDGE_DIR must not be the project root or one of its parent folders."
        )
    if not root.exists():
        raise LocalKnowledgeFolderError(
            f"Local knowledge folder does not exist yet: {root}. "
            "Download the materials and set LOCAL_KNOWLEDGE_DIR."
        )
    if not root.is_dir():
        raise LocalKnowledgeFolderError(
            f"LOCAL_KNOWLEDGE_DIR is not a folder: {root}"
        )
    return root


def _hash_file(path: Path) -> tuple[str, os.stat_result]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(HASH_BLOCK_SIZE), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise LocalFileChangedError("File changed while it was being scanned")
    return digest.hexdigest(), after


def _safe_member_name(info: zipfile.ZipInfo) -> Optional[str]:
    name = info.filename.replace("\\", "/").rstrip("/")
    if (
        not name
        or name.startswith("/")
        or PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or ":" in name
        or "\x00" in name
    ):
        return None
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limit_bytes: int,
) -> tuple[bytes, str, int]:
    if info.flag_bits & 0x1:
        raise RuntimeError("Encrypted/password-protected ZIP entries are not supported")
    if info.file_size > limit_bytes:
        raise _ArchiveLimitError(
            f"ZIP entry size {info.file_size} exceeds the {limit_bytes}-byte limit"
        )

    content = io.BytesIO()
    digest = hashlib.sha256()
    size = 0
    with archive.open(info, "r") as source:
        for block in iter(lambda: source.read(HASH_BLOCK_SIZE), b""):
            size += len(block)
            if size > limit_bytes:
                raise _ArchiveLimitError(
                    f"ZIP entry expanded beyond the {limit_bytes}-byte limit"
                )
            digest.update(block)
            content.write(block)
    return content.getvalue(), digest.hexdigest(), size


def _hash_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limit_bytes: int,
) -> tuple[str, int]:
    if info.flag_bits & 0x1:
        raise RuntimeError("Encrypted/password-protected ZIP entries are not supported")
    if info.file_size > limit_bytes:
        raise _ArchiveLimitError(
            f"ZIP entry size {info.file_size} exceeds the {limit_bytes}-byte limit"
        )

    digest = hashlib.sha256()
    size = 0
    with archive.open(info, "r") as source:
        for block in iter(lambda: source.read(HASH_BLOCK_SIZE), b""):
            size += len(block)
            if size > limit_bytes:
                raise _ArchiveLimitError(
                    f"ZIP entry expanded beyond the {limit_bytes}-byte limit"
                )
            digest.update(block)
    return digest.hexdigest(), size


def _record_local_file(
    result: LocalScanResult,
    first_by_hash: dict[str, str],
    relative: str,
    name: str,
    mime_type: str,
    kind: str,
    size: int,
    modified_time: str,
    source_hash: str,
    *,
    local_path: Optional[Path] = None,
    archive_path: Optional[Path] = None,
    archive_members: tuple[str, ...] = (),
) -> None:
    relative_parent = PurePosixPath(relative).parent.as_posix()
    folder_path = "" if relative_parent == "." else relative_parent
    file_id = "local-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()
    raw = {
        "name": name,
        "source_type": "local",
        "source_path": relative,
        "source_file_hash": source_hash,
    }
    if local_path is not None:
        raw["local_path"] = str(local_path)
    if archive_path is not None:
        raw["archive_path"] = str(archive_path)
        raw["archive_members"] = list(archive_members)
        result.archive_files += 1
    result.files.append(
        DriveFile(
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            kind=kind,
            folder_path=folder_path,
            modified_time=modified_time,
            md5=None,
            size=size,
            web_link=None,
            raw=raw,
        )
    )
    duplicate_of = first_by_hash.setdefault(source_hash, relative)
    if duplicate_of != relative:
        result.duplicates.append({"path": relative, "duplicate_of": duplicate_of})


def _scan_archive(
    archive_source: Path | io.BytesIO,
    archive_path: Path,
    archive_display_path: str,
    parent_members: tuple[str, ...],
    depth: int,
    root_modified_time: str,
    settings: Settings,
    result: LocalScanResult,
    first_by_hash: dict[str, str],
    budget: dict[str, int],
) -> None:
    result.archives += 1
    try:
        with zipfile.ZipFile(archive_source, "r") as archive:
            names: set[str] = set()
            for info in archive.infolist():
                budget["entries"] += 1
                if budget["entries"] > MAX_ARCHIVE_ENTRIES:
                    result.errors.append(
                        _issue(
                            archive_display_path,
                            f"Archive entry count exceeds {MAX_ARCHIVE_ENTRIES}.",
                        )
                    )
                    break

                member = _safe_member_name(info)
                if member is None:
                    result.errors.append(
                        _issue(
                            f"{archive_display_path}!/{info.filename}",
                            "Unsafe ZIP member path; entry was skipped.",
                        )
                    )
                    continue
                if info.is_dir():
                    continue
                if member.casefold() in names:
                    result.errors.append(
                        _issue(
                            f"{archive_display_path}!/{member}",
                            "Duplicate ZIP member path; ambiguous entry was skipped.",
                        )
                    )
                    continue
                names.add(member.casefold())

                mode = (info.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(mode):
                    result.errors.append(
                        _issue(
                            f"{archive_display_path}!/{member}",
                            "Symbolic-link ZIP entries are not followed.",
                        )
                    )
                    continue
                if info.flag_bits & 0x1:
                    result.inaccessible.append(
                        _issue(
                            f"{archive_display_path}!/{member}",
                            "Encrypted/password-protected ZIP entry is not supported.",
                        )
                    )
                    continue

                source_path = f"{archive_display_path}!/{member}"
                file_type = PurePosixPath(member).suffix.lower()
                if file_type == ".zip":
                    if depth >= MAX_ARCHIVE_DEPTH:
                        result.errors.append(
                            _issue(
                                source_path,
                                f"Nested ZIP depth exceeds {MAX_ARCHIVE_DEPTH}.",
                            )
                        )
                        continue
                    remaining = MAX_ARCHIVE_EXPANDED_BYTES - budget["expanded"]
                    nested_limit = min(MAX_NESTED_ARCHIVE_BYTES, remaining)
                    try:
                        nested_bytes, _digest, size = _read_zip_member(
                            archive, info, nested_limit
                        )
                        budget["expanded"] += size
                        _scan_archive(
                            io.BytesIO(nested_bytes),
                            archive_path,
                            source_path,
                            parent_members + (info.filename,),
                            depth + 1,
                            root_modified_time,
                            settings,
                            result,
                            first_by_hash,
                            budget,
                        )
                    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, EOFError) as exc:
                        result.errors.append(
                            _issue(source_path, f"{type(exc).__name__}: {exc}")
                        )
                    continue

                supported = SUPPORTED_LOCAL_TYPES.get(file_type)
                if supported is None:
                    result.unsupported.append(
                        {"path": source_path, "file_type": file_type or "no extension"}
                    )
                    continue

                member_limit = settings.max_download_mb * 1024 * 1024
                remaining = MAX_ARCHIVE_EXPANDED_BYTES - budget["expanded"]
                if info.file_size > member_limit:
                    result.oversized.append(
                        {
                            "path": source_path,
                            "size_bytes": info.file_size,
                            "limit_bytes": member_limit,
                        }
                    )
                    continue
                if info.file_size > remaining:
                    result.errors.append(
                        _issue(
                            archive_display_path,
                            "Archive expanded-size budget was reached; remaining entries were skipped.",
                        )
                    )
                    break
                try:
                    source_hash, size = _hash_zip_member(
                        archive, info, min(member_limit, remaining)
                    )
                    budget["expanded"] += size
                except _ArchiveLimitError as exc:
                    result.oversized.append(
                        {
                            "path": source_path,
                            "size_bytes": info.file_size,
                            "limit_bytes": min(member_limit, remaining),
                        }
                    )
                    result.errors.append(_issue(source_path, str(exc)))
                    continue
                except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, EOFError) as exc:
                    result.errors.append(
                        _issue(source_path, f"{type(exc).__name__}: {exc}")
                    )
                    continue

                mime_type, kind = supported
                _record_local_file(
                    result,
                    first_by_hash,
                    source_path,
                    PurePosixPath(member).name,
                    mime_type,
                    kind,
                    size,
                    root_modified_time,
                    source_hash,
                    archive_path=archive_path,
                    archive_members=parent_members + (info.filename,),
                )
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, EOFError) as exc:
        result.errors.append(
            _issue(archive_display_path, f"{type(exc).__name__}: {exc}")
        )


def scan_local_folder(
    root: str | Path,
    settings: Settings,
) -> LocalScanResult:
    """Scan supported files without database writes, downloads, or embeddings."""
    resolved_root = _validate_root(Path(root))
    result = LocalScanResult(root=resolved_root)
    first_by_hash: dict[str, str] = {}

    def on_walk_error(exc: OSError) -> None:
        path = str(getattr(exc, "filename", "") or resolved_root)
        try:
            relative = _relative_path(resolved_root, Path(path))
        except ValueError:
            relative = path
        result.inaccessible.append(_issue(relative, f"{type(exc).__name__}: {exc}"))
        log.warning("Cannot read local knowledge folder path %s: %s", relative, exc)

    max_bytes = settings.max_download_mb * 1024 * 1024
    for current, dirs, filenames in os.walk(
        resolved_root,
        topdown=True,
        followlinks=False,
        onerror=on_walk_error,
    ):
        current_path = Path(current)
        safe_dirs = []
        for directory in sorted(dirs):
            child = current_path / directory
            relative = _relative_path(resolved_root, child)
            if child.is_symlink():
                result.inaccessible.append(
                    _issue(relative, "Symbolic-link directories are not followed.")
                )
            else:
                safe_dirs.append(directory)
        dirs[:] = safe_dirs

        for filename in sorted(filenames):
            path = current_path / filename
            relative = _relative_path(resolved_root, path)
            if path.is_symlink():
                result.inaccessible.append(
                    _issue(relative, "Symbolic-link files are not followed.")
                )
                continue

            file_type = path.suffix.lower()
            if file_type == ".zip":
                try:
                    archive_stat = path.stat()
                    if not stat.S_ISREG(archive_stat.st_mode):
                        result.unsupported.append(
                            {
                                "path": relative,
                                "file_type": file_type,
                                "reason": "Not a regular file",
                            }
                        )
                        continue
                    if archive_stat.st_size > MAX_ARCHIVE_COMPRESSED_BYTES:
                        result.oversized.append(
                            {
                                "path": relative,
                                "size_bytes": archive_stat.st_size,
                                "limit_bytes": MAX_ARCHIVE_COMPRESSED_BYTES,
                            }
                        )
                        continue
                    _scan_archive(
                        path,
                        path,
                        relative,
                        (),
                        1,
                        datetime.fromtimestamp(
                            archive_stat.st_mtime, timezone.utc
                        ).isoformat(),
                        settings,
                        result,
                        first_by_hash,
                        {"entries": 0, "expanded": 0},
                    )
                    current_stat = path.stat()
                    if (
                        archive_stat.st_size,
                        archive_stat.st_mtime_ns,
                    ) != (current_stat.st_size, current_stat.st_mtime_ns):
                        result.errors.append(
                            _issue(relative, "ZIP file changed while it was being scanned.")
                        )
                except OSError as exc:
                    result.inaccessible.append(
                        _issue(relative, f"{type(exc).__name__}: {exc}")
                    )
                continue

            supported = SUPPORTED_LOCAL_TYPES.get(file_type)
            if supported is None:
                result.unsupported.append(
                    {"path": relative, "file_type": file_type or "no extension"}
                )
                continue

            try:
                file_stat = path.stat()
                if not stat.S_ISREG(file_stat.st_mode):
                    result.unsupported.append(
                        {"path": relative, "file_type": file_type, "reason": "Not a regular file"}
                    )
                    continue
                if file_stat.st_size > max_bytes:
                    result.oversized.append(
                        {
                            "path": relative,
                            "size_bytes": file_stat.st_size,
                            "limit_bytes": max_bytes,
                        }
                    )
                    continue
                source_hash, file_stat = _hash_file(path)
            except OSError as exc:
                result.inaccessible.append(
                    _issue(relative, f"{type(exc).__name__}: {exc}")
                )
                continue

            mime_type, kind = supported
            modified_time = datetime.fromtimestamp(
                file_stat.st_mtime, timezone.utc
            ).isoformat()
            _record_local_file(
                result,
                first_by_hash,
                relative,
                filename,
                mime_type,
                kind,
                file_stat.st_size,
                modified_time,
                source_hash,
                local_path=path,
            )

    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.status = (
        "partial"
        if result.inaccessible or result.oversized or result.errors
        else "complete"
    )
    return result


class LocalFileClient:
    """Implements the downloader contract used by the shared document processor."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def _open_archive_member(self, file: dict):
        archive_path = Path(file["archive_path"])
        members = file["archive_members"]
        if not members or archive_path.is_symlink():
            raise LocalFileChangedError("ZIP source is no longer a regular archive")
        if archive_path.stat().st_size > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise LocalFileChangedError("ZIP source exceeds the configured archive limit")

        with ExitStack() as stack:
            archive_source: Path | io.BytesIO = archive_path
            for index, name in enumerate(members):
                archive = stack.enter_context(zipfile.ZipFile(archive_source, "r"))
                info = archive.getinfo(name)
                if _safe_member_name(info) is None or info.flag_bits & 0x1:
                    raise LocalFileChangedError("ZIP member is unsafe or inaccessible")
                if index == len(members) - 1:
                    yield stack.enter_context(archive.open(info, "r"))
                    return
                if PurePosixPath(name.replace("\\", "/")).suffix.lower() != ".zip":
                    raise LocalFileChangedError("Nested archive chain changed after scanning")
                nested_bytes, _digest, _size = _read_zip_member(
                    archive, info, MAX_NESTED_ARCHIVE_BYTES
                )
                archive_source = stack.enter_context(io.BytesIO(nested_bytes))

    def download(self, file: dict, destination: Path) -> str:
        expected_hash = file["source_file_hash"]
        digest = hashlib.sha256()
        written = 0
        limit = self.settings.max_download_mb * 1024 * 1024
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if "archive_path" in file:
                source_context = self._open_archive_member(file)
            else:
                source = Path(file["local_path"])
                if source.is_symlink():
                    raise LocalFileChangedError("Local source became a symbolic link")
                source_context = source.open("rb")
            with source_context as input_file, destination.open("wb") as output_file:
                for block in iter(lambda: input_file.read(HASH_BLOCK_SIZE), b""):
                    written += len(block)
                    if written > limit:
                        raise LocalFileChangedError(
                            "File grew beyond MAX_DOWNLOAD_MB after scanning"
                        )
                    digest.update(block)
                    output_file.write(block)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if digest.hexdigest() != expected_hash:
            destination.unlink(missing_ok=True)
            raise LocalFileChangedError(
                "File changed after scanning; run the local scan again"
            )
        return Path(file["name"]).suffix
