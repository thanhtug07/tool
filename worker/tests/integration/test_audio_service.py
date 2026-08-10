"""Integration tests for AudioService (TASK-012) against real media fixtures.

These invoke real ffmpeg/ffprobe and are skipped when ffmpeg is unavailable.
The no-audio and longer cancel fixtures are generated in-test with ffmpeg (they
cannot be committed: fixtures must stay small and binary-free).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.core.ffmpeg import E_FFMPEG_FAILED, FFmpegError
from src.core.job import CancelledError, CancellationToken
from src.services.audio_service import extract_audio

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "media"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg/ffprobe not available on PATH",
)


def _run(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)


def _audio_spec(path: Path) -> dict:
    out = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=sample_rate,channels,codec_name",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(out.stdout.decode("utf-8"))
    return data["streams"][0]


def _make_no_audio_clip(tmp_path: Path) -> Path:
    target = tmp_path / "noaudio.mp4"
    _run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ]
    )
    return target


def test_extract_from_mp4_yields_16k_mono_pcm(tmp_path) -> None:
    out = tmp_path / "out.wav"
    result = extract_audio(str(FIXTURES / "tiny_h264.mp4"), str(out))
    assert out.is_file()
    assert result.file_size_bytes > 0
    assert result.duration_seconds is not None and result.duration_seconds > 1.5
    spec = _audio_spec(out)
    assert spec["sample_rate"] == "16000"
    assert spec["channels"] == 1
    assert spec["codec_name"] == "pcm_s16le"


def test_extract_from_mkv(tmp_path) -> None:
    out = tmp_path / "out.wav"
    result = extract_audio(str(FIXTURES / "tiny_h264.mkv"), str(out))
    assert out.is_file()
    assert result.duration_seconds is not None
    spec = _audio_spec(out)
    assert spec["sample_rate"] == "16000"
    assert spec["channels"] == 1


def test_progress_callback_reaches_completion(tmp_path) -> None:
    out = tmp_path / "out.wav"
    fractions: list[float] = []
    extract_audio(
        str(FIXTURES / "tiny_h264.mp4"),
        str(out),
        total_duration_seconds=2.0,
        on_progress=fractions.append,
    )
    assert fractions, "expected at least one progress callback"
    assert max(fractions) == 1.0, "extraction must report completion"


def test_no_audio_track_raises_clear_error(tmp_path) -> None:
    clip = _make_no_audio_clip(tmp_path)
    with pytest.raises(FFmpegError) as excinfo:
        extract_audio(str(clip), str(tmp_path / "out.wav"))
    assert excinfo.value.code == E_FFMPEG_FAILED
    assert "no audio" in excinfo.value.message.lower()


def test_cancel_before_start_aborts(tmp_path) -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancelledError):
        extract_audio(
            str(FIXTURES / "tiny_h264.mp4"),
            str(tmp_path / "out.wav"),
            cancel=token,
        )


def test_missing_input_file_raises(tmp_path) -> None:
    with pytest.raises(FFmpegError) as excinfo:
        extract_audio(str(tmp_path / "does-not-exist.mp4"), str(tmp_path / "out.wav"))
    assert excinfo.value.code == E_FFMPEG_FAILED


def test_path_injection_is_rejected_before_running(tmp_path) -> None:
    evil_output = str(tmp_path / "out;rm -rf .wav")
    with pytest.raises(FFmpegError):
        extract_audio(str(FIXTURES / "tiny_h264.mp4"), evil_output)
    assert not Path(evil_output).exists()
