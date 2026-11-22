"""Validate local workflow configuration without printing secrets or making mutations."""
import importlib.util
import sys

from app.config.settings import PROJECT_ROOT, Settings, get_settings

REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "pydantic_settings",
    "pymongo",
    "google.genai",
    "google.auth",
    "google_auth_httplib2",
    "googleapiclient.discovery",
    "httplib2",
    "fitz",
    "docx",
    "pptx",
    "pytesseract",
    "PIL",
)


def validate_local_configuration(settings: Settings) -> list[str]:
    issues = [
        f"Missing required setting: {name}"
        for name in settings.missing_for("api", "gemini", "local")
    ]
    root = settings.local_knowledge_path
    project_root = PROJECT_ROOT.resolve()
    if root == project_root or project_root.is_relative_to(root):
        issues.append(
            "LOCAL_KNOWLEDGE_DIR must not be the project root or one of its parent folders."
        )
    elif not root.exists():
        issues.append(
            "LOCAL_KNOWLEDGE_DIR folder does not exist yet; download/copy the "
            "materials and set the folder path."
        )
    elif not root.is_dir():
        issues.append("LOCAL_KNOWLEDGE_DIR does not point to a folder.")
    return issues


def missing_dependencies() -> list[str]:
    missing = []
    for module_name in REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            found = None
        if found is None:
            missing.append(module_name)
    return missing


def main() -> int:
    settings = get_settings()
    print(f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    issues = validate_local_configuration(settings)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1

    missing = missing_dependencies()
    if missing:
        print(f"ERROR: Required Python dependencies are missing: {', '.join(missing)}")
        print("Activate the project .venv and install requirements.txt.")
        return 1
    print("[OK] Required configuration is present; secret values are hidden.")
    print("[OK] Required local workflow dependencies are importable.")
    print("[OK] Local knowledge folder exists.")

    try:
        from app.vectorstore.mongo_client import get_db

        get_db(settings).command("ping")
    except Exception as exc:
        print(f"ERROR: MongoDB connectivity check failed ({type(exc).__name__}).")
        return 1
    print("[OK] MongoDB connectivity verified (read-only ping).")
    print(
        f"[OK] Gemini configuration present: model={settings.embedding_model}, "
        f"dimensions={settings.embedding_dimensions}; no embedding request was made."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
