"""Verify the local API/database and print actual retrieved source snippets."""
import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config.settings import get_settings


def _request_json(url: str, secret: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=data)
    if secret:
        request.add_header("X-RAG-Secret", secret)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def run(
    api_url: str, query: str, secret: str, status_only: bool = False
) -> int:
    base = api_url.rstrip("/")
    try:
        health = _request_json(f"{base}/health")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"ERROR: FastAPI health check failed ({type(exc).__name__}).")
        return 1
    if health.get("service") != "sahyog-college-rag":
        print("ERROR: Health endpoint did not identify the Sahyog RAG service.")
        return 1
    print("[OK] FastAPI health endpoint.")

    try:
        secure = _request_json(f"{base}/health/secure", secret=secret)
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"ERROR: Secure health check failed ({type(exc).__name__}).")
        return 1
    if not secure.get("mongo_connected"):
        print("ERROR: FastAPI cannot connect to MongoDB.")
        return 1
    if not secure.get("gemini_key_configured"):
        print("ERROR: Gemini API key is not configured in FastAPI.")
        return 1
    print("[OK] FastAPI MongoDB ping and Gemini configuration.")

    try:
        stats = _request_json(f"{base}/stats", secret=secret)
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"ERROR: Document/chunk count check failed ({type(exc).__name__}).")
        return 1
    print(
        "MongoDB counts: "
        f"{stats.get('total_documents', 0)} documents, "
        f"{stats.get('total_chunks', 0)} chunks, "
        f"{stats.get('approved_documents', 0)} approved documents."
    )
    if not stats.get("approved_documents", 0):
        print(
            "No approved documents are available. Approve indexed documents "
            "through the admin workflow before running a search smoke test."
        )
        return 2
    if status_only:
        print("Approved documents are available; search was not run.")
        return 0

    try:
        result = _request_json(
            f"{base}/search",
            secret=secret,
            body={"query": query, "top_k": 5},
        )
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"ERROR: Sample RAG search failed ({type(exc).__name__}).")
        return 1
    sources = result.get("results", [])
    if not sources:
        print("Search completed, but no matching approved sources were returned.")
        return 2

    print(f"\nSearch results for: {query}")
    for source in sources:
        page = source.get("page_number")
        page_label = f"page {page}" if page is not None else "page not available"
        snippet = " ".join(source.get("text", "").split())[:360]
        print(f"\n- {source.get('document_name', 'Unknown source')} — {page_label}")
        print(f"  {snippet}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--query",
        default="What are the academic rules and requirements?",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="report readiness and approval counts without running a search",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.rag_service_secret:
        print("ERROR: RAG_SERVICE_SECRET is not configured; secret value is hidden.")
        return 1
    return run(
        args.api_url,
        args.query,
        settings.rag_service_secret,
        status_only=args.status_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
