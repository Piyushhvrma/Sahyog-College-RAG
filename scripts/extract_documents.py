"""Run download -> extract -> clean -> chunk WITHOUT Gemini or MongoDB (inspect quality before paying for embeddings).
Writes data/extracted/<file_id>.json. Works on Drive files or a local folder.

  python -m scripts.extract_documents --limit 5
  python -m scripts.extract_documents --local-dir C:\\path\\to\\pdfs"""
import argparse
import json
import tempfile
from pathlib import Path

from app.ingestion.chunker import chunk_pages
from app.ingestion.cleaner import clean_pages
from app.ingestion.loader import load_document
from app.ingestion.metadata import extract_metadata
from scripts._common import bootstrap

EXT_KIND = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".txt": "txt", ".csv": "csv"}


def _dump(s, file_id, name, folder, kind, path):
    pages = clean_pages(load_document(path, kind, s))
    chunks = chunk_pages(pages, s.chunk_target_tokens, s.chunk_overlap_tokens)
    out = {"file_id": file_id, "file_name": name, "folder_path": folder, "kind": kind,
           "metadata": extract_metadata(folder, name), "pages": len(pages),
           "ocr_pages": sum(p.ocr for p in pages),
           "chunks": [{"chunk_index": c.chunk_index, "page_number": c.page_number,
                       "heading": c.heading, "text": c.text} for c in chunks]}
    (s.extracted_dir / f"{file_id}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                                     encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--local-dir", type=Path, default=None)
    args = ap.parse_args()
    s = bootstrap() if args.local_dir else bootstrap("drive")
    s.extracted_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0

    if args.local_dir:
        files = [p for p in sorted(args.local_dir.rglob("*")) if p.suffix.lower() in EXT_KIND]
        for p in files[:args.limit]:
            try:
                o = _dump(s, p.stem, p.name, str(p.parent.relative_to(args.local_dir)).replace("\\", "/").strip("."),
                          EXT_KIND[p.suffix.lower()], p)
                print(f"[OK]   {p.name}: {o['pages']} pages, {len(o['chunks'])} chunks, OCR pages={o['ocr_pages']}")
                ok += 1
            except Exception as exc:
                print(f"[FAIL] {p.name}: {exc}")
                fail += 1
    else:
        from app.drive.client import EXPORT_MAP, DriveClient
        from app.drive.folder_scanner import scan_folder
        client = DriveClient(s)
        scan = scan_folder(client, s.drive_root_folder_id)
        with tempfile.TemporaryDirectory() as tmp:
            for f in scan.files[:args.limit]:
                try:
                    ext = EXPORT_MAP.get(f.mime_type, (None, Path(f.name).suffix))[1]
                    local = Path(tmp) / f"{f.file_id}{ext}"
                    client.download(f.raw, local)
                    o = _dump(s, f.file_id, f.name, f.folder_path, f.kind, local)
                    print(f"[OK]   {f.name}: {o['pages']} pages, {len(o['chunks'])} chunks, OCR pages={o['ocr_pages']}")
                    ok += 1
                except Exception as exc:
                    print(f"[FAIL] {f.name}: {exc}")
                    fail += 1
    print(f"\nDone. ok={ok} failed={fail}. JSON in {s.extracted_dir}")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
