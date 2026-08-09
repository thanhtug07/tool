"""Integration tests: sidecar session-token auth over the real ASGI app.

TASK-006: Rust hands the worker a per-session token over stdin; the worker must
authenticate ``/health`` with that token (not the dev placeholder).
"""

from fastapi.testclient import TestClient

from src.api.routes import configure_auth_token
from src.main import app

client = TestClient(app)

SESSION_TOKEN = "session-token-3"


def test_health_authenticates_with_configured_session_token():
    try:
        configure_auth_token(SESSION_TOKEN)
        response = client.get("/health", headers={"Authorization": f"Bearer {SESSION_TOKEN}"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        configure_auth_token(None)


def test_health_rejects_placeholder_when_session_token_configured():
    try:
        configure_auth_token(SESSION_TOKEN)
        response = client.get("/health", headers={"Authorization": "Bearer dev-placeholder-token"})
        assert response.status_code == 401
    finally:
        configure_auth_token(None)


def test_health_rejects_request_without_token_when_session_token_configured():
    try:
        configure_auth_token(SESSION_TOKEN)
        response = client.get("/health")
        assert response.status_code == 401
    finally:
        configure_auth_token(None)
