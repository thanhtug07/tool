"""Unit tests for Phase 9/10 HTTP Range Media Streaming Routes."""

import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from src.main import create_app
from src.core.config import get_data_dir

app = create_app()
client = TestClient(app)

_DATA_DIR = Path(get_data_dir()).resolve()


def test_media_stream_file_not_found():
    """Requesting a non-existent file in data dir returns 404."""
    target = _DATA_DIR / "does_not_exist_9z3x.mp4"
    response = client.get(f"/api/media/stream?path={target}")
    assert response.status_code == 404


def test_media_stream_path_traversal_rejected():
    """Requesting a file outside the data dir returns 403."""
    response = client.get("/api/media/stream?path=C:/Windows/System32/drivers/etc/hosts")
    assert response.status_code == 403


def test_media_stream_full_file():
    """Full file streaming returns 200 with full content."""
    dummy = _DATA_DIR / "test_stream.mp4"
    dummy.parent.mkdir(parents=True, exist_ok=True)
    dummy.write_bytes(b"0123456789" * 100)  # 1000 bytes
    try:
        response = client.get(f"/api/media/stream?path={dummy}")
        assert response.status_code == 200
        assert len(response.content) == 1000
    finally:
        dummy.unlink(missing_ok=True)


def test_media_stream_partial_range():
    """Range request returns 206 with correct Content-Range header."""
    dummy = _DATA_DIR / "test_range.mp4"
    dummy.parent.mkdir(parents=True, exist_ok=True)
    dummy.write_bytes(b"0123456789" * 100)  # 1000 bytes
    try:
        headers = {"Range": "bytes=0-99"}
        response = client.get(f"/api/media/stream?path={dummy}", headers=headers)
        assert response.status_code == 206
        assert response.headers["Content-Range"] == "bytes 0-99/1000"
        assert len(response.content) == 100
    finally:
        dummy.unlink(missing_ok=True)
