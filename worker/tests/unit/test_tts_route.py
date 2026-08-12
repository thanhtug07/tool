"""Route tests for ``POST /v1/tts/synthesize`` (dubbing voice track).

The service is monkeypatched so no network / model download happens; these
tests exercise the HTTP surface (auth, validation, error envelope, response
shape).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import src.services.tts_service as tts_service
from src.api.routes import PLACEHOLDER_TOKEN
from src.main import app

client = TestClient(app)
AUTH_HEADER = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}


def _fake_synthesize(*args, **kwargs):
    return tts_service.TTSResult(
        voice_track_path="C:/tmp/voice_track.wav",
        meta_path="C:/tmp/tts_meta.json",
        engine_used="edge",
        voice_used="vi-VN-HoaiMyNeural",
    )


def _body() -> dict:
    return {
        "cues": [{"start": 0.0, "end": 2.0, "text": "xin chào"}],
        "voice": None,
        "engine": "edge",
        "language": "vi",
        "duration_seconds": 10.0,
        "output_dir": "C:/tmp",
    }


def test_route_requires_bearer_token() -> None:
    response = client.post("/v1/tts/synthesize", json=_body())
    assert response.status_code == 401


def test_route_validates_request_body() -> None:
    response = client.post("/v1/tts/synthesize", json={}, headers=AUTH_HEADER)
    assert response.status_code == 422


def test_route_empty_cues_rejected() -> None:
    body = _body()
    body["cues"] = []
    response = client.post("/v1/tts/synthesize", json=body, headers=AUTH_HEADER)
    assert response.status_code == 422


def test_route_happy_path(monkeypatch) -> None:
    monkeypatch.setattr(tts_service, "synthesize_cues", _fake_synthesize)
    response = client.post("/v1/tts/synthesize", json=_body(), headers=AUTH_HEADER)
    assert response.status_code == 200
    payload = response.json()
    assert payload["voice_track_path"] == "C:/tmp/voice_track.wav"
    assert payload["cue_count"] == 1
    assert payload["engine_used"] == "edge"
    assert payload["voice_used"] == "vi-VN-HoaiMyNeural"


def test_route_service_error_returns_envelope(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise tts_service.TTSError(tts_service.E_TTS_UNAVAILABLE, "Unknown voice")

    monkeypatch.setattr(tts_service, "synthesize_cues", boom)
    response = client.post("/v1/tts/synthesize", json=_body(), headers=AUTH_HEADER)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "E_TTS_UNAVAILABLE"
    assert body["error"]["message"]
    assert "recoverable" in body["error"]
