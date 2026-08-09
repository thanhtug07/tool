"""Unit tests: worker app importability and health schema (no HTTP)."""

from src import __version__
from src.api.schemas import HealthResponse
from src.main import app


def test_app_is_importable_without_starting_server():
    assert app.title == "AI Video Localization Worker"


def test_health_schema_is_deterministic():
    first = HealthResponse(status="ok", version=__version__, gpu=None)
    second = HealthResponse(status="ok", version=__version__, gpu=None)
    assert first.model_dump() == second.model_dump()


def test_health_schema_fields():
    payload = HealthResponse(status="ok", version=__version__, gpu=None).model_dump()
    assert set(payload) == {"status", "version", "gpu"}
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert payload["gpu"] is None
