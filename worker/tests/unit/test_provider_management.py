"""Provider Management tests: /v1/providers/test + the FREE provider kind.

Runs without ffmpeg (unlike the pipeline route tests) so the provider registry
surface is testable anywhere. Gemini's live call is stubbed with an injected
client; mock/local/free paths run for real.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.pipeline import build_translation_provider
from src.api.routes import PLACEHOLDER_TOKEN
from src.main import app
from src.services.providers.base import ProviderError

client = TestClient(app)

AUTH = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}


def _test(kind: str, config: dict | None = None, api_key: str | None = None):
    return client.post(
        "/v1/providers/test",
        json={
            "provider_kind": kind,
            "provider_config": config,
            "api_key": api_key,
        },
        headers=AUTH,
    )


def test_provider_test_requires_auth():
    resp = client.post("/v1/providers/test", json={"provider_kind": "mock"})
    assert resp.status_code in (401, 403)


def test_mock_always_available():
    resp = _test("mock")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "latency_ms" in body


def test_unknown_kind_fails_with_provider_unavailable():
    resp = _test("nope")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_PROVIDER_UNAVAILABLE"


def test_free_without_local_server_fails_explicitly():
    resp = _test("free")
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "E_PROVIDER_UNAVAILABLE"
    assert "local LLM server" in err["message"]


def test_free_with_unreachable_server_fails():
    resp = _test("free", {"server_url": "http://127.0.0.1:1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_API_ERROR"


def test_local_with_unreachable_server_fails():
    resp = _test("local", {"server_url": "http://127.0.0.1:1"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_API_ERROR"


def test_gemini_without_key_fails_with_missing_key():
    resp = _test("gemini")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_API_KEY_MISSING"


def test_gemini_auth_error_surfaces(monkeypatch):
    class FakeClient:
        class Models:
            def get(self, model):  # noqa: ARG002
                class _Resp(Exception):
                    rest_code = 401

                raise _Resp()

        models = Models()

    class FakeGemini:
        def __init__(self, **kwargs):
            self.api_key = kwargs["api_key"]
            self.model_name = kwargs.get("model") or "gemini-2.5-flash-lite"

        def _resolve_client(self):
            return FakeClient()

    import src.services.providers.translation.gemini_provider as gp

    monkeypatch.setattr(gp, "GeminiProvider", FakeGemini)
    resp = _test("gemini", {"model": "gemini-2.5-flash"}, api_key="bad-key")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "E_API_AUTH"


def test_gemini_success_with_injected_client(monkeypatch):
    class FakeClient:
        class Models:
            def get(self, model):
                assert model == "gemini-2.5-flash-lite"
                return object()

        models = Models()

    class FakeGemini:
        def __init__(self, **kwargs):
            self.api_key = kwargs["api_key"]
            self.model_name = kwargs.get("model") or "gemini-2.5-flash-lite"

        def _resolve_client(self):
            return FakeClient()

    import src.services.providers.translation.gemini_provider as gp

    monkeypatch.setattr(gp, "GeminiProvider", FakeGemini)
    resp = _test("gemini", api_key="valid-key")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---- factory: FREE kind ---------------------------------------------------


def test_free_factory_requires_server_or_model():
    try:
        build_translation_provider("free", {}, None)
    except ProviderError as exc:
        assert exc.code == "E_PROVIDER_UNAVAILABLE"
    else:
        raise AssertionError("FREE without a server must raise")


def test_free_factory_resolves_to_local_llm_provider():
    provider = build_translation_provider(
        "free",
        {"server_url": "http://127.0.0.1:8080", "model": "qwen"},
        None,
    )
    assert provider.name == "local"
    assert provider.model == "qwen"


def test_local_factory_maps_server_url():
    provider = build_translation_provider(
        "local",
        {"server_url": "http://127.0.0.1:8080", "model_path": "C:/models/q4.gguf"},
        None,
    )
    assert provider.name == "local"
