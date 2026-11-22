"""Shared-secret protection. Only the Express backend should know X-RAG-Secret."""
import hmac

from fastapi import Depends, Header, HTTPException, status

from app.config.settings import Settings, get_settings


def secrets_match(provided: str | None, expected: str) -> bool:
    """Constant-time comparison. Empty expected secret never matches."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_secret(
    x_rag_secret: str | None = Header(default=None, alias="X-RAG-Secret"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.rag_service_secret:
        # Fail closed: a misconfigured server must not be open.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Service secret not configured")
    if not secrets_match(x_rag_secret, settings.rag_service_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing service secret")
