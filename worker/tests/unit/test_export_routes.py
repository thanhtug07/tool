"""Unit tests: export HTTP routes (TASK-029) via FastAPI TestClient.

Covers the request → worker-service → canonical error-envelope contract.
The subtitle export path is exercised for real (pure file copy/conversion);
the video export path uses ``run_qc=False`` so no ffprobe is required here
(the ffprobe-backed QC path is covered by the integration suite).

Note: the routes use the *real* filesystem because the worker is a local
sidecar — the contract under test is exactly what the Rust core will call.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.routes import PLACEHOLDER_TOKEN
from src.main import app

client = TestClient(app)

AUTH_HEADER = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}


def _export_headers() -> dict[str, str]:
    return AUTH_HEADER


def test_export_video_requires_bearer_token(tmp_path: Path) -> None:
    response = client.post(
        "/v1/export/video",
        json={"source_video": "x.mp4", "target_dir": str(tmp_path)},
    )
    assert response.status_code == 401


def test_export_video_copies_file_without_qc(tmp_path: Path) -> None:
    source = tmp_path / "rendered.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"B" * 4096)
    out_dir = tmp_path / "out"

    response = client.post(
        "/v1/export/video",
        headers=_export_headers(),
        json={
            "source_video": str(source),
            "target_dir": str(out_dir),
            "name": "final",
            "run_qc": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"].endswith("final.mp4")
    assert body["qc"]["passed"] is True
    assert body["qc"]["issues"] == []
    assert (out_dir / "final.mp4").is_file()


def test_export_video_missing_source_returns_error_envelope(tmp_path: Path) -> None:
    response = client.post(
        "/v1/export/video",
        headers=_export_headers(),
        json={
            "source_video": str(tmp_path / "nope.mp4"),
            "target_dir": str(tmp_path),
        },
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "E_EXPORT_INVALID"
    assert error["message"]
    assert error["recoverable"] is False
    # No stack traces / paths leak into the envelope.
    assert "Traceback" not in response.text


def test_export_subtitles_passthrough_and_conversion(tmp_path: Path) -> None:
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    passthrough = client.post(
        "/v1/export/subtitles",
        headers=_export_headers(),
        json={"source_subtitle": str(srt), "target_dir": str(out_dir), "format": "srt"},
    )
    converted = client.post(
        "/v1/export/subtitles",
        headers=_export_headers(),
        json={"source_subtitle": str(srt), "target_dir": str(out_dir), "format": "vtt"},
    )

    assert passthrough.status_code == 200
    assert converted.status_code == 200
    srt_path = Path(passthrough.json()["path"])
    vtt_path = Path(converted.json()["path"])
    assert srt_path.read_text(encoding="utf-8") == srt.read_text(encoding="utf-8")
    vtt = vtt_path.read_text(encoding="utf-8")
    assert "WEBVTT" in vtt
    assert "00:00:01.000 --> 00:00:02.000" in vtt


def test_export_subtitles_ass_conversion_is_rejected(tmp_path: Path) -> None:
    ass = tmp_path / "subtitle.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")

    response = client.post(
        "/v1/export/subtitles",
        headers=_export_headers(),
        json={"source_subtitle": str(ass), "target_dir": str(tmp_path), "format": "srt"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_EXPORT_INVALID"


def test_export_subtitles_missing_source_returns_error_envelope(tmp_path: Path) -> None:
    response = client.post(
        "/v1/export/subtitles",
        headers=_export_headers(),
        json={"source_subtitle": str(tmp_path / "nope.srt"), "target_dir": str(tmp_path)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_EXPORT_INVALID"
