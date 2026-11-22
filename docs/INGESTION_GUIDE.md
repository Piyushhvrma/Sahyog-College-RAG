# Ingestion Guide — how knowledge gets into the system

## The big picture (and what each word means)
| Stage | What actually happens | Where | Cost |
|---|---|---|---|
| **1. Choose a source** | Local files preserve the downloaded hierarchy; Google Drive remains an optional separate source. | local computer or Google Drive | free |
| **2. Read/Export** | Local files are read in place. The optional Drive sync exports Google Docs/Sheets/Slides to supported formats. | local CLI or Drive worker | free |
| **3. Extract text** | PDF/DOCX/PPTX/CSV/TXT → plain text per page/slide. Scanned pages go through OCR. | local CLI or Drive worker | free |
| **4. Clean** | Fix hyphenation, strip page numbers and repeated headers/footers. | local CLI or Cron worker | free |
| **5. Chunk** | Split into ~800-token passages with 100-token overlap; never across pages. | local CLI or Cron worker | free |
| **6. Metadata** | Branch/semester/subject/year/type read from folder + file names (never guessed). | local CLI or Cron worker | free |
| **7. Embed** | Gemini turns each chunk into a 768-number vector capturing meaning. | Gemini API | quota |
| **8. Store** | Chunks + vectors saved in MongoDB Atlas. | Atlas | storage |
| **9. Retrieve** | A question is embedded; vector + keyword search find the best chunks; RRF merges them. | FastAPI + Atlas | tiny |
| **10. Generate** | Express sends the chunks + question to Groq, which writes the answer with citations. | Groq | Groq quota |

**This is not fine-tuning.** Groq's model is never changed. The documents sit in a database; the model just reads the relevant excerpts each time. To "teach" it a new fact you add a document — no retraining.

Local-folder ingestion and the optional Drive workflow reuse the existing processing, embedding, MongoDB, and approval pipeline. Local-folder ingestion runs on the computer with the downloaded materials and writes to Atlas; deploying the API does not upload those files. For optional Drive sync, see `docs/DEPLOYMENT.md`; do not mount OAuth credentials or enable Drive jobs unless you intend to use that source.

## Local knowledge folder

Set `LOCAL_KNOWLEDGE_DIR` in your existing `.env` to the root of the downloaded folder hierarchy. The `.env.example` default is `./knowledge`, relative to the project. Set the value to an absolute path if your materials are stored elsewhere; do not overwrite an existing `.env`. The project-local `knowledge\` folder is Git-ignored.

```powershell
.\.venv\Scripts\python.exe -m scripts.scan_local
.\.venv\Scripts\python.exe -m scripts.ingest_local
.\.venv\Scripts\python.exe -m scripts.local_smoke_test
```

`scan_local` is read-only: it does not call Gemini or write to MongoDB. It recursively lists supported paths and reports unsupported, byte-identical duplicate, unreadable, and oversized files plus new/modified/unchanged estimates when MongoDB is configured. `ingest_local` asks for confirmation before writing; `--yes` is intended only for a caller such as the confirmation-gated `RUN_SAHYOG_RAG.bat`. Local IDs are stable for a relative path, source hashes detect byte changes, unchanged files are skipped, failed files are recorded, and missing local files never trigger deletion reconciliation. The original relative path is stored with the document and its chunks.

ZIP files are scanned without extracting members into the knowledge directory. Supported PDF, DOCX, PPTX, TXT, and CSV members are processed through the regular pipeline. Nested archives are bounded to depth 3; each outer archive is limited to 512 MiB compressed, 1 GiB expanded, and 10,000 entries. Nested archive members are limited to 50 MiB, and document members still obey `MAX_DOWNLOAD_MB`. Unsafe paths, encrypted/corrupt entries, and limit violations are reported as scan issues. Archive paths are retained in source metadata for citations.

The batch launcher validates the existing Python 3.12/3.13 virtual environment, dependencies and required settings, performs a read-only MongoDB ping and local scan, asks before ingestion, and starts the API in a separate terminal. It does not call any Google Drive scanner or ingestion command. Swagger is normally available at `http://127.0.0.1:8000/docs` in development mode.

New local files always remain pending approval; the existing approval state is preserved on updates. The default remains `AUTO_APPROVE=false`. Approve them through the existing Sahyog admin workflow before expecting search results. The local smoke test reports actual sources/snippets and explicitly stops before search when no approved documents exist.

## Organise Drive for best metadata
Metadata is read from the folder path and file name. Recommended:
```
NITRR Knowledge/                  <-- DRIVE_ROOT_FOLDER_ID
├── CSE/
│   ├── Semester 5/
│   │   ├── Operating Systems/
│   │   │   ├── PYQ/2023-24/CS-301 End Sem.pdf
│   │   │   └── Notes/Unit 3 Deadlocks.pptx
├── Placements/2024-25/Placement Statistics.pdf
├── Notices/Exam Schedule Nov 2025.pdf
└── Academic/Attendance Rules.pdf
```
Detected automatically: branch (CSE, ECE, EE, ME, CE …), semester (`Semester 5`, `Sem 5`, `5th sem`), academic year (`2023-24`), subject (folder right after the semester folder, unless it's a generic label like PYQ/Notes), course code (`CS-301`), and document type (pyq, syllabus, timetable, placement, attendance, notice, notes, regulation). **Anything not found stays empty** — it is never invented.

## Step-by-step (PowerShell)
Always start with:
```powershell
cd C:\path\to\Sahyog-College-RAG
.\.venv\Scripts\Activate.ps1
```
**Step 1 – Verify Drive access** (browser opens the first time only)
```powershell
python -m scripts.test_drive
```
Illustrative output:
```
[OK] Authenticated as: Your Name <you@gmail.com>
[OK] Root folder: NITRR Knowledge (id=1AbC…)
     - CSE  [folder]
     - Placements  [folder]
[OK] Token saved at: C:\…\secrets\token.json
```
**Step 2 – See what will be ingested (read-only scan; no downloads)**
```powershell
python -m scripts.scan_drive
```
```
Folders scanned : 14
Supported files : 62
Unsupported     : 3
Inaccessible    : 0
Transient errors: 0
Other errors    : 0
Scan status     : complete
Scan timestamp  : 2026-01-01T12:00:00+00:00
By type: {'pdf': 48, 'pptx': 9, 'docx': 5}
```
The JSON report also records scan timestamps, inaccessible items, and transient errors. Inaccessible shortcuts are retried during the next scan. A partial scan still indexes discovered accessible files but never runs deletion reconciliation.
**Step 3 – Check extraction quality *before* spending embeddings** (optional but wise)
```powershell
python -m scripts.extract_documents --limit 5
```
Open `data\extracted\<id>.json` and read the text. If a scanned PDF has few chunks, install Tesseract (below).

**Step 4 – Prepare MongoDB** (once)
```powershell
python -m scripts.setup_mongo
```
Wait until both Atlas indexes are READY.

**Step 5 – Test Gemini** (once)
```powershell
python -m scripts.test_embeddings
```
**Step 6 – Dry run, then a small real run**
```powershell
python -m scripts.ingest --dry-run
python -m scripts.ingest --limit 3
python -m scripts.inspect_chunks --name "Attendance"
```
**Step 7 – Full ingestion**
```powershell
python -m scripts.ingest
```
Illustrative output:
```
Scanning Drive …
  62 supported files, 3 unsupported, 0 inaccessible, 14 folders
  [1/62] CSE/Semester 5/Operating Systems/PYQ/2023-24/CS-301 End Sem.pdf
    -> new (7 chunks)
  …
    -> FAILED: ExtractionError: PDF is password-protected
=== Summary ===
{ "scanned": 62, "new": 58, "duplicates": 2, "failed": 2, "chunks_written": 611, … }
```
A failed file never stops the run; it's recorded (visible in the admin panel and `inspect_chunks`).

**Step 8 – Test retrieval**
```powershell
python -m scripts.test_retrieval
```

## Adding new PDFs later (no full reprocess)
1. Drop the PDFs into the right Drive folder.
2. Run:
```powershell
python -m scripts.sync_drive
```
The sync compares each file's Drive checksum/modified time and the embedding model/dimensions. **Unchanged files are skipped**; only new/changed ones are downloaded and embedded. File failures and inaccessible targets are recorded and retried on future runs. Files deleted from Drive are removed from search only after a fully complete scan. Identical content uploaded twice is detected (`duplicate`) and not embedded again.

New documents default to pending approval. Keep `AUTO_APPROVE=false`; approve indexed items using the protected `PATCH /documents/{file_id}/approval` endpoint. An authorized administrator can run the exact PowerShell command shown in [API_REFERENCE.md](./API_REFERENCE.md).

For deployment, configure one external Cron Job using `python -m scripts.sync_drive`, a valid refreshable OAuth token, `DRIVE_INTERACTIVE_AUTH=false`, and the same MongoDB/Gemini/Drive settings as the API. See [DEPLOYMENT.md](./DEPLOYMENT.md).

Other commands:
| Situation | Command |
|---|---|
| Retry only files that failed | `python -m scripts.ingest --only-failed` |
| Preview changes | `python -m scripts.ingest --dry-run` |
| Changed embedding model/dimensions | update `.env`, recreate the vector index, then `python -m scripts.reindex --yes` |
| Hide a document from students | Admin panel → disable (or `PATCH /documents/{id}/approval`) |

## Tesseract OCR on Windows (only for scanned PDFs)
1. Download the Windows installer from https://github.com/UB-Mannheim/tesseract/wiki and run it (default path `C:\Program Files\Tesseract-OCR\`).
2. Either add that folder to PATH, **or** set in `.env`:
```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```
3. Verify: `& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version`
OCR runs per page, only when a page has almost no text layer. English is default; for Hindi pages, tick *Hindi* in the installer's language list and (if needed) adjust the `lang` in `app/ingestion/loader.py` (`pytesseract.image_to_string(img, lang="eng+hin")`).

## What you must NOT do
* Don't paste PDF text into MongoDB by hand — the pipeline does it.
* Don't change `EMBEDDING_MODEL`/`EMBEDDING_DIMENSIONS` after ingesting without running `reindex` — old vectors would be incompatible.
* Don't commit `secrets\` or `.env`.

## Known extraction limits
* Google Sheets export contains **only the first sheet** (Drive CSV export limitation).
* DOCX has no reliable page numbers → citations show the document name only.
* Handwritten or low-quality scans produce poor OCR.
* Password-protected PDFs are reported as failed.
* Very large files over `MAX_DOWNLOAD_MB` are skipped and logged.
