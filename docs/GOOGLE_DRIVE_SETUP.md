# Google Drive setup (Desktop OAuth, read-only)

You already created the `College-RAG` project, enabled the Drive API and created a **Desktop** OAuth client. Remaining steps:

1. Google Cloud Console → *APIs & Services → Credentials* → your Desktop client → **Download JSON**.
2. Save it as `secrets\credentials.json` inside the project (create the folder: `New-Item -ItemType Directory secrets`).
3. In `.env`:
```
GOOGLE_CREDENTIALS_PATH=./secrets/credentials.json
GOOGLE_TOKEN_PATH=./secrets/token.json
DRIVE_ROOT_FOLDER_ID=<id>
MAX_DOWNLOAD_MB=50
```
4. Get the folder ID: open the folder in Drive → copy the URL `https://drive.google.com/drive/folders/<ID>?usp=sharing` → the ID is between `/folders/` and `?`.
5. Run `python -m scripts.test_drive`. A browser opens; sign in with the account that can open the folder; approve *read-only* access. A refreshable token is saved to `secrets\token.json` and reused afterwards.

## ⚠ Token expiry in "Testing" mode
If the OAuth consent screen is **External + Testing**, Google expires refresh tokens after **7 days**. The code handles this: when a refresh fails it re-opens the browser flow. To avoid weekly logins, either:
* Publish the app (*OAuth consent screen → Publish app*; for personal use you can click through the "unverified app" warning), or
* Use *Internal* if your account belongs to a Google Workspace organisation.
Also add your Google account under **Test users** while in Testing mode.

## Permissions behaviour
* Scope is `drive.readonly`. The service can only read what the signed-in account can read; files it can't open are **logged as inaccessible and skipped**, never bypassed.
* Shared drives are included (`supportsAllDrives`).
* Shortcuts are followed when the target is accessible; loops are prevented.
* Google Docs → DOCX, Slides → PPTX, Sheets → CSV (first sheet only).
* If any folder is inaccessible, the "deleted files" cleanup pass is skipped so nothing is wrongly removed.

## Never share
`credentials.json`, `token.json`, `.env`. All are in `.gitignore` and excluded from the ZIP.
