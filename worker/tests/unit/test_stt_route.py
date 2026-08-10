"""Route tests for ``POST /v1/stt/transcribe`` (TASK-013).

These exercise the HTTP surface (auth, request validation, error envelope)
without any AI model — no `ai` marker needed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.routes import PLACEHOLDER_TOKEN
from src.main import app

client = TestClient(app)

AUTH_HEADER = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}


def test_route_requires_bearer_token() -> None:
    response = client.post("/v1/stt/transcribe", json={"audio_path": "a.wav", "project_id": "p"})
    assert response.status_code == 401


def test_route_validates_request_body() -> None:
    response = client.post("/v1/stt/transcribe", json={}, headers=AUTH_HEADER)
    assert response.status_code == 422


def test_route_missing_audio_returns_error_envelope() -> None:
    response = client.post(
        "/v1/stt/transcribe",
        json={"audio_path": "definitely-not-there.wav", "project_id": "p"},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "E_STT_FAILED"
    assert body["error"]["message"]
    assert "recoverable" in body["error"]
