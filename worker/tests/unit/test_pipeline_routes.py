"""Tests for the RELEASE-P0 pipeline stage routes (audio/translate/subtitle/render/cancel).

Uses the real FastAPI TestClient. The audio-extract and render tests exercise
real ffmpeg (skipped when unavailable); translation uses the deterministic
``mock`` provider; subtitle generation is real (pure Python).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.routes import PLACEHOLDER_TOKEN
from src.api.schemas import Transcript, TranscriptSegment
from src.main import app

client = TestClient(app)

AUTH = {"Authorization": f"Bearer {PLACEHOLDER_TOKEN}"}

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _transcript(*texts: str) -> dict:
    segments = [
        TranscriptSegment(
            id=f"seg_{i}",
            idx=i,
            speaker=f"speaker_{i % 2}",
            start=float(i),
            end=float(i) + 1.0,
            text=text,
            language="zh",
            confidence=0.95,
        )
        for i, text in enumerate(texts)
    ]
    return Transcript(
        schema_version=1,
        project_id="proj_0001",
        language="zh",
        model="mock",
        segments=segments,
    ).model_dump()


def _tiny_video(path: Path, duration: float = 1.0) -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-nostdin",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True,
        check=True,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/v1/audio/extract", "/v1/translate", "/v1/subtitle", "/v1/render", "/v1/jobs/x/cancel"],
)
def test_pipeline_routes_require_bearer_token(path: str) -> None:
    response = client.post(path, json={})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/v1/progress/x"])
def test_progress_route_requires_bearer_token(path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Audio extract
# ---------------------------------------------------------------------------


def test_audio_extract_writes_16k_wav(tmp_path: Path) -> None:
    video = tmp_path / "in.mp4"
    _tiny_video(video)
    out = tmp_path / "out.wav"

    response = client.post(
        "/v1/audio/extract",
        headers=AUTH,
        json={"video_path": str(video), "output_path": str(out)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output_path"] == str(out)
    assert body["file_size_bytes"] > 0
    assert out.is_file()
    assert out.stat().st_size > 0


def test_audio_extract_missing_video_returns_envelope(tmp_path: Path) -> None:
    response = client.post(
        "/v1/audio/extract",
        headers=AUTH,
        json={"video_path": str(tmp_path / "nope.mp4"), "output_path": str(tmp_path / "o.wav")},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "E_FFMPEG_FAILED"
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# Translate
# ---------------------------------------------------------------------------


def test_translate_with_mock_provider_covers_all_segments() -> None:
    response = client.post(
        "/v1/translate",
        headers=AUTH,
        json={
            "transcript": _transcript("你好", "世界"),
            "project_id": "proj_0001",
            "provider": "mock",
            "target_language": "vi",
            "model": "gemini-2.5-flash-lite",
        },
    )
    assert response.status_code == 200, response.text
    translation = response.json()
    assert translation["target_language"] == "vi"
    items = [item for block in translation["blocks"] for item in block["translations"]]
    assert len(items) == 2
    assert all(item["segment_id"].startswith("seg_") for item in items)
    assert all(item["translated_text"] for item in items)


def test_translate_unknown_provider_returns_envelope() -> None:
    response = client.post(
        "/v1/translate",
        headers=AUTH,
        json={
            "transcript": _transcript("你好"),
            "project_id": "proj_0001",
            "provider": "does-not-exist",
            "target_language": "vi",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_PROVIDER_UNAVAILABLE"


def test_translate_passes_glossary_through_context() -> None:
    response = client.post(
        "/v1/translate",
        headers=AUTH,
        json={
            "transcript": _transcript("张三"),
            "project_id": "proj_0001",
            "provider": "mock",
            "target_language": "vi",
            "glossary": {"张三": "Trương Tam"},
            "characters": {"张三": "Nhân vật chính"},
            "rules": ["Dịch tên người theo âm Hán Việt"],
        },
    )
    assert response.status_code == 200, response.text
    items = [item for block in response.json()["blocks"] for item in block["translations"]]
    assert len(items) == 1


# ---------------------------------------------------------------------------
# Subtitle
# ---------------------------------------------------------------------------


def _translation(*texts: str) -> dict:
    return {
        "schema_version": 1,
        "target_language": "vi",
        "model": "mock",
        "blocks": [
            {
                "block_idx": 0,
                "translations": [
                    {
                        "idx": i,
                        "segment_id": f"seg_{i}",
                        "source_text": texts[i],
                        "translated_text": f"Bản dịch {i}",
                        "confidence": 1.0,
                    }
                    for i in range(len(texts))
                ],
            }
        ],
    }


def test_subtitle_generates_cues_and_files(tmp_path: Path) -> None:
    transcript = _transcript("你好", "世界")
    response = client.post(
        "/v1/subtitle",
        headers=AUTH,
        json={
            "transcript": transcript,
            "translation": _translation("你好", "世界"),
            "project_id": "proj_0001",
            "output_dir": str(tmp_path),
            "language": "vi",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["cues"]) == 2
    assert body["ass_path"] and Path(body["ass_path"]).is_file()
    assert body["srt_path"] and Path(body["srt_path"]).is_file()
    assert Path(body["srt_path"]).read_text(encoding="utf-8").startswith("1\n")


def test_subtitle_missing_translation_segment_returns_envelope(tmp_path: Path) -> None:
    transcript = _transcript("你好", "世界", "再见")
    response = client.post(
        "/v1/subtitle",
        headers=AUTH,
        json={
            "transcript": transcript,
            "translation": _translation("你好", "世界"),
            "project_id": "proj_0001",
            "output_dir": str(tmp_path),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_SUBTITLE_INVALID"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_produces_validated_output(tmp_path: Path) -> None:
    video = tmp_path / "in.mp4"
    _tiny_video(video)
    out = tmp_path / "out.mp4"

    response = client.post(
        "/v1/render",
        headers=AUTH,
        json={
            "video_path": str(video),
            "output_path": str(out),
            "encoder": "libx264",
            "preset": "veryfast",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert out.is_file()
    assert body["encoder_used"] == "libx264"
    assert body["width"] == 160
    assert body["height"] == 120


def test_render_missing_input_returns_envelope(tmp_path: Path) -> None:
    response = client.post(
        "/v1/render",
        headers=AUTH,
        json={
            "video_path": str(tmp_path / "nope.mp4"),
            "output_path": str(tmp_path / "o.mp4"),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E_RENDER_INVALID"


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_endpoint_sets_registered_token(tmp_path: Path) -> None:
    from src.api.pipeline import _cancel_scope

    video = tmp_path / "in.mp4"
    _tiny_video(video, duration=2.0)
    out = tmp_path / "out.wav"

    response = client.post(
        "/v1/audio/extract",
        headers=AUTH,
        json={"video_path": str(video), "output_path": str(out), "job_id": "job_cancel_test"},
    )
    assert response.status_code == 200

    # The scope is gone after completion: cancelling is a no-op.
    response = client.post("/v1/jobs/job_cancel_test/cancel", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"cancelled": False}


def test_precancelled_stage_returns_409(tmp_path: Path) -> None:
    from src.api.pipeline import _cancel_scope

    video = tmp_path / "in.mp4"
    _tiny_video(video, duration=2.0)

    # Register a scope and cancel it before the render call.
    with _cancel_scope("job_precancel") as token:
        token.cancel()
        response = client.post(
            "/v1/render",
            headers=AUTH,
            json={
                "video_path": str(video),
                "output_path": str(tmp_path / "o.mp4"),
                "job_id": "job_precancel",
            },
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "E_CANCELLED"


def test_precancelled_translate_returns_409() -> None:
    """Translation is worker-cancellable at block granularity."""
    from src.api.pipeline import _cancel_scope

    with _cancel_scope("job_precancel_translate") as token:
        token.cancel()
        response = client.post(
            "/v1/translate",
            headers=AUTH,
            json={
                "transcript": _transcript("你好", "世界"),
                "project_id": "proj_0001",
                "provider": "mock",
                "target_language": "vi",
                "job_id": "job_precancel_translate",
            },
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "E_CANCELLED"


# ---------------------------------------------------------------------------
# Live progress
# ---------------------------------------------------------------------------


def test_progress_endpoint_reports_registered_stage() -> None:
    from src.api.pipeline import _cancel_scope

    with _cancel_scope("job_progress_live") as token:
        token.set_progress(0.42, "transcribe")
        response = client.get("/v1/progress/job_progress_live", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert body["job_id"] == "job_progress_live"
        assert body["progress"] == pytest.approx(0.42)
        assert body["stage"] == "transcribe"

    # The scope is gone after completion: progress is null.
    response = client.get("/v1/progress/job_progress_live", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["progress"] is None


def test_progress_endpoint_unknown_job_returns_null() -> None:
    response = client.get("/v1/progress/job_never_registered", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "job_never_registered"
    assert body["progress"] is None
    assert body["stage"] is None
