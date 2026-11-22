@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not enter the Sahyog-College-RAG project folder.
    pause
    exit /b 1
)

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: The existing .venv was not found. Create it and install requirements.txt first.
    pause
    exit /b 1
)
"%PYTHON_EXE%" -c "import sys; assert sys.version_info[:2] in ((3, 12), (3, 13)), 'Use Python 3.12 or 3.13 in the project .venv.'"
if errorlevel 1 (
    echo ERROR: The project .venv must use Python 3.12 or 3.13.
    pause
    exit /b 1
)

echo.
echo Checking local settings, dependencies, MongoDB and embedding configuration...
"%PYTHON_EXE%" -m scripts.check_local_setup
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo Running a read-only local folder scan...
"%PYTHON_EXE%" -m scripts.scan_local
if errorlevel 1 (
    echo ERROR: Local scan failed or no supported files were found. No ingestion was started.
    pause
    exit /b 1
)

echo.
choice /C YN /M "Proceed with local ingestion? This may call Gemini and write to MongoDB"
if errorlevel 2 (
    echo Ingestion cancelled. No documents or chunks were written.
    exit /b 0
)

echo.
echo Ingesting only LOCAL_KNOWLEDGE_DIR. Google Drive scanning is not used.
"%PYTHON_EXE%" -m scripts.ingest_local --yes
if errorlevel 1 (
    echo ERROR: Local ingestion reported failures or a partial scan. The API was not started.
    pause
    exit /b 1
)

echo.
echo Starting FastAPI in a separate terminal window...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if ($health.service -eq 'sahyog-college-rag') { exit 0 } } catch { }; exit 1"
if errorlevel 1 (
    start "Sahyog RAG API" /D "%CD%" "%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) else (
    echo Existing Sahyog RAG API is already healthy; not starting a duplicate.
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline=(Get-Date).AddSeconds(30); do { try { $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if ($health.service -eq 'sahyog-college-rag') { exit 0 } } catch { }; Start-Sleep -Seconds 1 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 (
    echo ERROR: FastAPI did not become healthy within 30 seconds. Check the separate API terminal.
    pause
    exit /b 1
)

echo.
echo Sahyog RAG is ready.
echo API:     http://127.0.0.1:8000
echo Health:  http://127.0.0.1:8000/health
echo Swagger: http://127.0.0.1:8000/docs  (development mode only)
"%PYTHON_EXE%" -m scripts.local_smoke_test --status-only
if errorlevel 2 (
    echo No approved documents are available yet. Approve indexed documents in the Sahyog admin panel.
) else if errorlevel 1 (
    echo ERROR: Could not verify API, MongoDB, or approval status.
    pause
    exit /b 1
)
echo Full search smoke test: "%PYTHON_EXE%" -m scripts.local_smoke_test
pause
exit /b 0
