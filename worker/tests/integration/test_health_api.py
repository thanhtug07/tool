"""Integration tests: HTTP surface via FastAPI TestClient.

These exercise the real ASGI app (routing + auth middleware) without Tauri or
any sidecar wiring — TASK-005 has no process lifecycle yet.
"""

import os

from fastapi.testclient import TestClient

from src.api.routes import PLACEHOLDER_TOKEN
from src.main import app

client = TestClient(app)

AUTH_HEADER = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}


def test_health_exists_and_returns_200():
    response = client.get("/health", headers=AUTH_HEADER)
    assert response.status_code == 200


def test_health_returns_expected_schema():
    response = client.get("/health", headers=AUTH_HEADER)
    assert response.json() == {"status": "ok", "version": "0.1.0", "gpu": None}


def test_health_is_deterministic():
    first = client.get("/health", headers=AUTH_HEADER).json()
    second = client.get("/health", headers=AUTH_HEADER).json()
    assert first == second


def test_health_does_not_leak_sensitive_information():
    response = client.get("/health", headers=AUTH_HEADER)
    body = response.text.lower()
    assert os.environ.get("WORKER_AUTH_TOKEN") is None or "worker_auth_token" not in body
    assert "secret" not in body
    assert "key" not in body
    assert "path" not in body
    assert "token" not in body


def test_health_requires_bearer_token():
    response = client.get("/health")
    assert response.status_code == 401


def test_health_rejects_wrong_token():
    response = client.get("/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_health_rejects_malformed_auth_scheme():
    response = client.get("/health", headers={"Authorization": PLACEHOLDER_TOKEN})
    assert response.status_code == 401
