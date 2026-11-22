import math
from types import SimpleNamespace

import httpx
import pytest

from app.embeddings.gemini_embedder import (
    EmbeddingError,
    EmbeddingQuotaError,
    EmbeddingRateLimitError,
    GeminiEmbedder,
    format_document,
    format_query,
    l2_normalize,
)


class FakeModels:
    def __init__(self, dims=768, fail_times=0, aggregate=False):
        self.calls, self.dims, self.fail_times, self.aggregate = [], dims, fail_times, aggregate

    def embed_content(self, model, contents, config):
        self.calls.append((model, list(contents), config))
        if self.fail_times > 0:
            self.fail_times -= 1
            from google.genai import errors
            raise errors.APIError(429, {"error": {"message": "rate", "status": "RESOURCE_EXHAUSTED"}})
        n = 1 if self.aggregate else len(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * self.dims) for _ in range(n)])


def emb(models, **env):
    e = GeminiEmbedder(client=SimpleNamespace(models=models))
    return e


def test_prompt_formats_for_embedding_2():
    assert format_query("hi") == "task: search result | query: hi"
    assert format_document("body", "T") == "title: T | text: body"
    assert format_document("body") == "title: none | text: body"


def test_query_sends_formatted_text_and_dims(monkeypatch):
    m = FakeModels()
    v = emb(m).embed_query("attendance")
    assert len(v) == 768 and math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6)
    model, contents, cfg = m.calls[0]
    assert model == "gemini-embedding-2" and contents == ["task: search result | query: attendance"]
    assert cfg.output_dimensionality == 768 and cfg.task_type is None


def test_batching(monkeypatch):
    monkeypatch.setenv("EMBED_BATCH_SIZE", "4")
    from app.config.settings import get_settings
    get_settings.cache_clear()
    m = FakeModels()
    out = emb(m).embed_documents([f"t{i}" for i in range(10)])
    assert len(out) == 10 and len(m.calls) == 3


def test_wrong_dimension_rejected():
    with pytest.raises(EmbeddingError, match="expected 768"):
        emb(FakeModels(dims=3072)).embed_query("x")


def test_empty_inputs():
    e = emb(FakeModels())
    with pytest.raises(ValueError):
        e.embed_query("  ")
    with pytest.raises(ValueError):
        e.embed_documents(["ok", ""])
    assert e.embed_documents([]) == []


def test_retry_on_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    m = FakeModels(fail_times=1)
    assert len(emb(m).embed_query("x")) == 768 and len(m.calls) == 2


def test_retry_after_header_is_respected(monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    class RetryAfterModels(FakeModels):
        def embed_content(self, model, contents, config):
            self.calls.append((model, list(contents), config))
            if len(self.calls) == 1:
                from google.genai import errors
                response = SimpleNamespace(headers={"Retry-After": "7"})
                raise errors.APIError(
                    429,
                    {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}},
                    response=response,
                )
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1] * 768)]
            )

    models = RetryAfterModels()
    assert len(emb(models).embed_query("x")) == 768
    assert 7.0 in sleeps
    assert len(models.calls) == 2


def test_retry_after_header_is_capped():
    from app.embeddings.gemini_embedder import _retry_after_seconds

    error = SimpleNamespace(
        response=SimpleNamespace(headers={"Retry-After": "120"})
    )
    assert _retry_after_seconds(error) == 30


def test_daily_quota_error_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    class DailyQuotaModels(FakeModels):
        def embed_content(self, model, contents, config):
            self.calls.append((model, list(contents), config))
            from google.genai import errors
            raise errors.APIError(
                429,
                {
                    "error": {
                        "message": "Quota exceeded",
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "quotaId": (
                                    "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
                                )
                            }
                        ],
                    }
                },
            )

    models = DailyQuotaModels()
    with pytest.raises(EmbeddingQuotaError, match="daily"):
        emb(models).embed_query("x")
    assert len(models.calls) == 1


def test_exhausted_transient_429_is_reported_as_rate_limit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    models = FakeModels(fail_times=99)

    with pytest.raises(EmbeddingRateLimitError):
        emb(models).embed_query("x")
    assert len(models.calls) == 2


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    models = FakeModels(fail_times=99)
    with pytest.raises(EmbeddingError):
        emb(models).embed_query("x")
    assert len(models.calls) == 2


def test_retries_temporary_transport_failure(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    class TransportModels(FakeModels):
        def embed_content(self, model, contents, config):
            self.calls.append((model, list(contents), config))
            if len(self.calls) == 1:
                raise httpx.ConnectError("temporary connection failure")
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1] * 768)]
            )

    models = TransportModels()
    assert len(emb(models).embed_query("x")) == 768
    assert len(models.calls) == 2


def test_gemini_client_has_finite_timeout_and_no_sdk_retries(monkeypatch):
    from google import genai
    from app.config.settings import Settings

    captured = {}
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    embedder = GeminiEmbedder(
        settings=Settings(_env_file=None, gemini_api_key="test-key")
    )

    _ = embedder.client

    options = captured["http_options"]
    assert options.timeout == 10_000
    assert options.retry_options.attempts == 1


def test_aggregated_batch_falls_back_to_single(monkeypatch):
    m = FakeModels(aggregate=True)
    out = emb(m).embed_documents(["a", "b", "c"])
    assert len(out) == 3


def test_missing_key():
    from app.config.settings import Settings

    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY"):
        GeminiEmbedder(settings=Settings(_env_file=None, gemini_api_key="")).client


def test_normalize_zero_vector():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]
