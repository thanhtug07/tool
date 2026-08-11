"""Integration: RenderService burns subtitles with ffmpeg and validates (TASK-027).

Acceptance: "RenderService nhúng phụ đề bằng libass, giữ nguyên resolution/FPS,
có progress/cancel, và chỉ trả về file đã validate". Skipped when ffmpeg is not
on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.core.ffmpeg import CancellationToken
from src.core.job import CancelledError
from src.services.render_service import (
    E_RENDER_INVALID,
    E_RENDER_VALIDATION,
    RenderConfig,
    RenderError,
    render,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "media"

FFMPEG = shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available on PATH")


@pytest.fixture()
def subtitle(tmp_path: Path) -> Path:
    ass = tmp_path / "sub.ass"
    ass.write_text(
        "[Script Info]\n"
        "PlayResX: 1920\nPlayResY: 1080\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,44,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,1,2,10,10,24,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        "Dialogue: 0,0:00:00.50,0:00:01.50,Default,,0,0,0,,HELLO WORLD\n"
        "Dialogue: 0,0:00:01.50,0:00:01.90,Default,,0,0,0,,BYE\n",
        encoding="utf-8",
    )
    return ass


def test_render_burns_subtitle_and_validates(tmp_path: Path, subtitle: Path) -> None:
    source = FIXTURES / "tiny_h264.mp4"
    output = tmp_path / "rendered.mp4"

    progress: list[float] = []
    result = render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            subtitle_path=str(subtitle),
            video_encoder="libx264",
            check_window=(0.5, 1.5),
        ),
        on_progress=lambda p: progress.append(p.fraction),
    )

    assert output.exists()
    assert output.stat().st_size > 0
    assert result.output_path == str(output)
    assert result.encoder_used == "libx264"
    assert result.width == 320
    assert result.height == 240
    assert result.fps == (25, 1)
    assert result.audio_streams == 1
    assert result.duration_seconds == pytest.approx(2.0, abs=0.2)
    assert progress and progress[-1] == pytest.approx(1.0, abs=0.01)


def test_render_without_subtitles_copies_stream(tmp_path: Path) -> None:
    source = FIXTURES / "tiny_h264.mp4"
    output = tmp_path / "copied.mp4"

    result = render(RenderConfig(input_path=str(source), output_path=str(output), video_encoder="libx264"))
    assert output.exists()
    assert result.fps == (25, 1)
    assert result.audio_streams == 1


def test_cancel_mid_render_cleans_up(tmp_path: Path, subtitle: Path) -> None:
    long_source = tmp_path / "long.mp4"
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=30",
            "-c:v", "libx264", "-preset", "veryfast",
            str(long_source),
        ],
        check=True,
    )
    token = CancellationToken()

    def _cancel_later() -> None:
        time.sleep(0.5)
        token.cancel()

    threading.Thread(target=_cancel_later, daemon=True).start()

    with pytest.raises(CancelledError):
        render(
            RenderConfig(
                input_path=str(long_source),
                output_path=str(tmp_path / "cancelled.mp4"),
                subtitle_path=str(subtitle),
                video_encoder="libx264",
            ),
            cancel=token,
        )

    assert not (tmp_path / "cancelled.mp4").exists()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("render_")]
    assert leftovers == []


def test_missing_input_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(tmp_path / "missing.mp4"),
                output_path=str(tmp_path / "out.mp4"),
            )
        )
    assert exc_info.value.code == E_RENDER_INVALID


def test_missing_subtitle_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(FIXTURES / "tiny_h264.mp4"),
                output_path=str(tmp_path / "out.mp4"),
                subtitle_path=str(tmp_path / "nope.ass"),
            )
        )
    assert exc_info.value.code == E_RENDER_INVALID


def test_output_same_as_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    shutil.copy(FIXTURES / "tiny_h264.mp4", source)
    with pytest.raises(RenderError) as exc_info:
        render(RenderConfig(input_path=str(source), output_path=str(source)))
    assert exc_info.value.code == E_RENDER_INVALID


def test_unsupported_requested_encoder_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(FIXTURES / "tiny_h264.mp4"),
                output_path=str(tmp_path / "out.mp4"),
                video_encoder="h264_not_a_real_encoder",
            )
        )
    assert exc_info.value.code == E_RENDER_INVALID


def test_hardware_failure_falls_back_to_libx264(tmp_path: Path, subtitle: Path, monkeypatch) -> None:
    import src.services.render_service as rs

    real_run = rs.run_ffmpeg
    hw_failures = {"count": 0}

    def fake_run(args, **kwargs):
        if "-c:v" in args and args[args.index("-c:v") + 1] == "h264_nvenc":
            hw_failures["count"] += 1
            return type("R", (), {"returncode": 1, "stderr": "hardware encoder failed"})()
        return real_run(args, **kwargs)

    monkeypatch.setattr(rs, "run_ffmpeg", fake_run)
    monkeypatch.setattr(rs, "available_video_encoders", lambda: ("h264_nvenc", "libx264"))

    output = tmp_path / "fallback.mp4"
    result = render(
        RenderConfig(
            input_path=str(FIXTURES / "tiny_h264.mp4"),
            output_path=str(output),
            subtitle_path=str(subtitle),
            video_encoder="h264_nvenc",
        )
    )
    assert hw_failures["count"] == 1
    assert result.encoder_used == "libx264"
    assert output.exists()


def test_validation_failure_never_ships_output(tmp_path: Path, monkeypatch) -> None:
    import src.services.render_service as rs

    monkeypatch.setattr(rs, "render_validation_issues", lambda source, output: ["forced validation failure"])

    output = tmp_path / "must_not_exist.mp4"
    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(FIXTURES / "tiny_h264.mp4"),
                output_path=str(output),
                video_encoder="libx264",
            )
        )
    assert exc_info.value.code == E_RENDER_VALIDATION
    assert not output.exists()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("render_")]
    assert leftovers == []
