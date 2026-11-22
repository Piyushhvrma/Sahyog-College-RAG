"""Step 1: authorise Google Drive (opens a browser the first time) and prove access to the root folder.
Usage: python -m scripts.test_drive"""
from googleapiclient.errors import HttpError

from app.drive.auth import DriveAuthError
from app.drive.client import DriveClient
from scripts._common import bootstrap


def main() -> int:
    s = bootstrap("drive")
    client = DriveClient(s)
    try:
        who = client.about()["user"]
        print(f"[OK] Authenticated as: {who.get('displayName')} <{who.get('emailAddress')}>")
        root = client.get_file(s.drive_root_folder_id)
        print(f"[OK] Root folder: {root['name']} (id={root['id']})")
        n = 0
        for child in client.list_children(s.drive_root_folder_id):
            kind = "folder" if child["mimeType"].endswith("folder") else child["mimeType"].split("/")[-1]
            print(f"     - {child['name']}  [{kind}]")
            n += 1
            if n >= 15:
                print("     … (showing first 15)")
                break
        print(f"[OK] Token saved at: {s.token_file}")
        return 0
    except DriveAuthError as exc:
        print(f"[FAIL] {exc}")
    except HttpError as exc:
        print(f"[FAIL] Drive API error: {exc}")
        print("       Check DRIVE_ROOT_FOLDER_ID and that this Google account can open the folder.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
