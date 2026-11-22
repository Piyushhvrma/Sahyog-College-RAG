from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.api.security import require_secret, secrets_match
from tests.conftest import SECRET


def test_secrets_match():
    assert secrets_match("abc", "abc")
    assert not secrets_match("abd", "abc")
    assert not secrets_match(None, "abc")
    assert not secrets_match("", "abc")
    assert not secrets_match("abc", "")          # empty expected never matches
    assert secrets_match("pässword", "pässword")  # non-ascii safe


def _client():
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(require_secret)])
    def x():
        return {"ok": True}
    return TestClient(app)


def test_missing_header_401():
    assert _client().get("/x").status_code == 401


def test_wrong_secret_401():
    assert _client().get("/x", headers={"X-RAG-Secret": "nope"}).status_code == 401


def test_right_secret_200():
    assert _client().get("/x", headers={"X-RAG-Secret": SECRET}).status_code == 200


def test_unconfigured_secret_fails_closed(monkeypatch):
    from app.config.settings import get_settings
    monkeypatch.setenv("RAG_SERVICE_SECRET", "")
    get_settings.cache_clear()
    assert _client().get("/x", headers={"X-RAG-Secret": ""}).status_code == 503


def test_uses_constant_time_compare():
    import inspect
    import app.api.security as sec
    assert "hmac.compare_digest" in inspect.getsource(sec)
