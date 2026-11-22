"""List everything the pipeline would see, without downloading. Writes data/reports/drive_scan.json.
Usage: python -m scripts.scan_drive"""
import json
from collections import Counter

from app.drive.client import DriveClient
from app.drive.folder_scanner import scan_folder
from app.ingestion.metadata import extract_metadata
from scripts._common import bootstrap


def main() -> int:
    s = bootstrap("drive")
    res = scan_folder(DriveClient(s), s.drive_root_folder_id)
    report = {
        "status": res.status,
        "started_at": res.started_at,
        "finished_at": res.finished_at,
        "folders_scanned": res.folders_scanned,
        "supported_files": len(res.files),
        "unsupported_files": len(res.unsupported),
        "inaccessible_items": res.inaccessible,
        "transient_failures": res.transient_failures,
        "errors": res.errors,
        "files": [
            {
                "id": f.file_id,
                "name": f.name,
                "kind": f.kind,
                "path": f.folder_path,
                "modified": f.modified_time,
                "size": f.size,
            }
            for f in res.files
        ],
        "unsupported": res.unsupported,
    }
    print(f"Folders scanned : {res.folders_scanned}")
    print(f"Supported files : {len(res.files)}")
    print(f"Unsupported     : {len(res.unsupported)}")
    print(f"Inaccessible    : {len(res.inaccessible)}")
    print(f"Transient errors: {len(res.transient_failures)}")
    print(f"Other errors    : {len(res.errors)}")
    print(f"Scan status     : {res.status}")
    print(f"Scan timestamp  : {res.started_at}")
    print("\nBy type:", dict(Counter(f.kind for f in res.files)))
    print("\nFirst 25 files with detected metadata:")
    for f in res.files[:25]:
        m = {k: v for k, v in extract_metadata(f.folder_path, f.name).items() if v is not None}
        print(f"  {f.folder_path or '/'}/{f.name}\n      {m}")
    for label, items in (
        ("Inaccessible items", res.inaccessible),
        ("Transient failures", res.transient_failures),
        ("Other scan errors", res.errors),
    ):
        if items:
            print(f"\n{label}:")
            for item in items:
                print(
                    f"  {item['path'] or item['id']}: "
                    f"{item['error_type']}: {item['reason']}"
                )
    out = s.reports_dir / "drive_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
