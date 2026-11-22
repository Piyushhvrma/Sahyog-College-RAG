"""Gemini embeddings via the google-genai SDK.

gemini-embedding-2 does NOT accept `task_type`; the task is written into the prompt text:
    queries   : "task: search result | query: <text>"
    documents : "title: <title or none> | text: <text>"
(see https://ai.google.dev/gemini-api/docs/embeddings). Vectors are truncated with
`output_dimensionality`, so we L2-normalise them ourselves.
"""
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from typing import Optional

import httpx

from app.config.settings import Settings, get_settings

log = logging.getLogger(__name__)
EMBEDDING_HTTP_TIMEOUT_MS = 10_000
EMBEDDING_MAX_ATTEMPTS = 2
MAX_RETRY_AFTER_SECONDS = 30


class EmbeddingError(RuntimeError):
    pass


class EmbeddingQuotaError(EmbeddingError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class EmbeddingRateLimitError(EmbeddingError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def _is_daily_quota_error(exc: Exception) -> bool:
    details = getattr(exc, "details", None)
    try:
        detail_text = json.dumps(details, default=str)
    except (TypeError, ValueError):
        detail_text = str(details)
    text = " ".join(
        (
            str(getattr(exc, "status", "")),
            str(getattr(exc, "message", "")),
            detail_text,
        )
    ).casefold()
    return any(
        marker in text
        for marker in ("perday", "per_day", "per-day", "per day", "daily quota")
    )


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def format_query(text: str) -> str:
    return f"task: search result | query: {text}"


def format_document(text: str, title: Optional[str] = None) -> str:
    return f"title: {title or 'none'} | text: {text}"


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec


class GeminiEmbedder:
    def __init__(self, settings: Settings | None = None, client=None):
        self.settings = settings or get_settings()
        self.model = self.settings.embedding_model
        self.dimensions = self.settings.embedding_dimensions
        self._client = client
        self._last_call = 0.0

    # -- plumbing ------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            if not self.settings.gemini_api_key:
                raise EmbeddingError("GEMINI_API_KEY is not set")
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=EMBEDDING_HTTP_TIMEOUT_MS,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()
            self._client = None

    @property
    def meta(self) -> dict:
        return {"embedding_model": self.model, "embedding_dimensions": self.dimensions}

    def _pace(self) -> None:
        wait = self.settings.embed_min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _call(
        self, contents: list[str], max_retries: int = EMBEDDING_MAX_ATTEMPTS
    ) -> list[list[float]]:
        from google.genai import errors, types
        cfg = types.EmbedContentConfig(output_dimensionality=self.dimensions)
        for attempt in range(max_retries):
            self._pace()
            try:
                resp = self.client.models.embed_content(model=self.model, contents=contents, config=cfg)
                return [list(e.values) for e in resp.embeddings]
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                retry_after = _retry_after_seconds(exc)
                if code == 429 and _is_daily_quota_error(exc):
                    raise EmbeddingQuotaError(
                        "Gemini daily embedding quota is exhausted.",
                        retry_after=retry_after,
                    ) from exc
                retryable = code in (429, 500, 502, 503, 504)
                if not retryable:
                    raise EmbeddingError(
                        f"Gemini API request failed (HTTP {code})."
                    ) from exc
                if attempt == max_retries - 1:
                    if code == 429:
                        raise EmbeddingRateLimitError(
                            "Gemini rate limit persisted after bounded retries.",
                            retry_after=retry_after,
                        ) from exc
                    raise EmbeddingError(
                        f"Gemini API request failed after bounded retries (HTTP {code})."
                    ) from exc
                delay = retry_after if retry_after is not None else min(2 ** attempt, 2)
                log.warning(
                    "Gemini API status %s; retrying in %ss (attempt %d/%d)",
                    code,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
            except httpx.TransportError as exc:
                if attempt == max_retries - 1:
                    raise EmbeddingError(
                        "Gemini transport failed after bounded retries."
                    ) from exc
                delay = min(2 ** attempt, 2)
                log.warning(
                    "Gemini transport failure; retrying in %ss (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
        raise EmbeddingError("Exhausted retries")

    def _validate(self, vec: list[float]) -> list[float]:
        if len(vec) != self.dimensions:
            raise EmbeddingError(
                f"Got {len(vec)}-dim vector, expected {self.dimensions}. "
                "Check EMBEDDING_DIMENSIONS and your Atlas index.")
        return l2_normalize(vec)

    def _embed_formatted(self, inputs: list[str]) -> list[list[float]]:
        vectors = self._call(inputs)
        if len(vectors) != len(inputs):
            # Some models aggregate a multi-item request into one vector; fall back to 1-by-1.
            log.warning("Batch returned %d vectors for %d inputs; embedding individually.",
                        len(vectors), len(inputs))
            vectors = []
            for item in inputs:
                single = self._call([item])
                if len(single) != 1:
                    raise EmbeddingError("Unexpected embedding count for single input")
                vectors.append(single[0])
        return [self._validate(v) for v in vectors]

    # -- public API ----------------------------------------------
    def embed_query(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("Cannot embed empty query")
        return self._embed_formatted([format_query(text.strip())])[0]

    def embed_documents(self, texts: list[str], titles: list[Optional[str]] | None = None) -> list[list[float]]:
        if not texts:
            return []
        if any(not t or not t.strip() for t in texts):
            raise ValueError("Cannot embed empty document text")
        titles = titles or [None] * len(texts)
        out: list[list[float]] = []
        bs = self.settings.embed_batch_size
        for i in range(0, len(texts), bs):
            batch = [format_document(t.strip(), ttl) for t, ttl in zip(texts[i:i + bs], titles[i:i + bs])]
            out.extend(self._embed_formatted(batch))
        return out
