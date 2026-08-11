"""Integration: RenderService burns subtitles with ffmpeg and validates (TASK-027).

Acceptance: "RenderService nhúng phụ đề bằng libass, giữ nguyên resolution/FPS,
có progress/cancel, và chỉ trả về file đã validate". Skipped when ffmpeg is not
on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.core.ffmpeg import CancellationToken
from src.core.job import CancelledError
from src.services.media_service import probe
from src.services.render_service import (
    E_RENDER_INVALID,
    E_RENDER_VALIDATION,
    ImageWatermark,
    RenderConfig,
    RenderError,
    SubtitleExportOptions,
    TextWatermark,
    WatermarkConfig,
    export_subtitles,
    export_video,
    frame_region_mean,
    render,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "media"

FFMPEG = shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available on PATH")


def _solid_video(path: Path, *, color: str = "black", duration: float = 1.0) -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=color={color}:s=320x240:r=25:d={duration}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )


def _solid_png(path: Path, *, color: str = "white") -> None:
    subprocess.run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=color={color}:s=64x64",
            "-frames:v", "1",
            str(path),
        ],
        check=True,
    )


def _region_lum(path: Path, time_s: float, region: tuple[float, float, float, float], width: int = 320, height: int = 240) -> float:
    completed = subprocess.run(
        [
            FFMPEG, "-nostdin",
            "-i", str(path), "-ss", f"{time_s:.3f}",
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return frame_region_mean(completed.stdout, width, height, region)


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


def test_render_with_text_watermark_draws_visible_text(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    output = tmp_path / "wm_text.mp4"
    plain = tmp_path / "plain.mp4"

    wm = WatermarkConfig(text=TextWatermark(text="WM XY", position="top-left", margin=4, font_size=88))
    result = render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            watermark=wm,
            video_encoder="libx264",
        )
    )
    render(
        RenderConfig(
            input_path=str(source),
            output_path=str(plain),
            video_encoder="libx264",
        )
    )

    region = (0.0, 0.0, 0.4, 0.4)
    assert result.encoder_used == "libx264"
    lum = _region_lum(output, 0.5, region)
    plain_lum = _region_lum(plain, 0.5, region)
    assert lum > plain_lum + 40.0


def test_render_with_image_watermark_overlays_png(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    logo = tmp_path / "logo.png"
    _solid_png(logo)

    output = tmp_path / "wm_image.mp4"
    result = render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            watermark=WatermarkConfig(
                image=ImageWatermark(image_path=str(logo), position="bottom-left", margin=8, width=64)
            ),
            video_encoder="libx264",
        )
    )
    assert result.width == 320
    assert result.height == 240

    corner_region = (0.0, 0.7, 0.3, 1.0)
    elsewhere = (0.7, 0.0, 1.0, 0.3)
    assert _region_lum(output, 0.5, corner_region) > 120.0
    assert _region_lum(output, 0.5, elsewhere) < 40.0


def test_render_with_image_watermark_opacity_scales(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    logo = tmp_path / "logo.png"
    _solid_png(logo)

    output = tmp_path / "wm_faded.mp4"
    render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            watermark=WatermarkConfig(
                image=ImageWatermark(image_path=str(logo), position="bottom-left", margin=8, width=64, opacity=0.5)
            ),
            video_encoder="libx264",
        )
    )
    faded_lum = _region_lum(output, 0.5, (0.0, 0.7, 0.3, 1.0))
    assert 50.0 < faded_lum < 120.0


def test_render_with_quote_text_watermark_uses_textfile(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    output = tmp_path / "wm_quote.mp4"

    wm = WatermarkConfig(text=TextWatermark(text="don't panic", position="top-left", margin=4, font_size=64))
    result = render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            watermark=wm,
            video_encoder="libx264",
        )
    )
    assert result.output_path == str(output)
    assert _region_lum(output, 0.5, (0.0, 0.0, 0.6, 0.4)) > 30.0


def test_render_with_text_and_image_watermark_combined(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    logo = tmp_path / "logo.png"
    _solid_png(logo)

    output = tmp_path / "wm_both.mp4"
    render(
        RenderConfig(
            input_path=str(source),
            output_path=str(output),
            watermark=WatermarkConfig(
                text=TextWatermark(text="PRO", position="top-left", margin=4, font_size=64),
                image=ImageWatermark(image_path=str(logo), position="bottom-right", margin=8, width=64),
            ),
            video_encoder="libx264",
        )
    )
    assert _region_lum(output, 0.5, (0.0, 0.0, 0.4, 0.4)) > 30.0
    assert _region_lum(output, 0.5, (0.7, 0.7, 1.0, 1.0)) > 120.0


def test_invalid_watermark_is_rejected_before_encode(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(source),
                output_path=str(tmp_path / "bad.mp4"),
                watermark=WatermarkConfig(
                    text=TextWatermark(text="x", position="middle")
                ),
            )
        )
    assert exc_info.value.code == E_RENDER_INVALID

    with pytest.raises(RenderError) as exc_info:
        render(
            RenderConfig(
                input_path=str(source),
                output_path=str(tmp_path / "bad2.mp4"),
                watermark=WatermarkConfig(
                    image=ImageWatermark(image_path=str(tmp_path / "missing.png"))
                ),
            )
        )
    assert exc_info.value.code == E_RENDER_INVALID
    assert not (tmp_path / "bad.mp4").exists()
    assert not (tmp_path / "bad2.mp4").exists()


def test_export_video_produces_valid_copy_with_passing_qc(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    out_dir = tmp_path / "exports"

    result = export_video(str(source), str(out_dir), name="final")

    assert os.path.isfile(result.path)
    assert result.path == os.path.join(str(out_dir), "final.mp4")
    assert result.qc.passed is True
    assert result.qc.issues == ()
    exported_meta = probe(result.path)
    assert exported_meta.width == 320
    assert exported_meta.height == 240
    assert exported_meta.audio_streams == []


def test_export_video_detects_duplicate_and_appends_suffix(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source)
    out_dir = tmp_path / "exports"

    first = export_video(str(source), str(out_dir), name="clip")
    second = export_video(str(source), str(out_dir), name="clip")

    assert first.path.endswith("clip.mp4")
    assert second.path.endswith("clip (1).mp4")
    assert os.path.isfile(second.path)


def test_export_qc_fails_when_source_is_corrupt(tmp_path: Path) -> None:
    source = tmp_path / "black.mp4"
    _solid_video(source, duration=2.0)
    out_dir = tmp_path / "exports"
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xde\xad\xbe\xef" * 512)

    with pytest.raises(RenderError) as exc_info:
        export_video(str(corrupt), str(out_dir), name="final")
    assert exc_info.value.code == E_RENDER_INVALID
    assert not os.path.exists(os.path.join(str(out_dir), "final.mp4"))


def test_export_subtitles_passthrough_and_conversion(tmp_path: Path) -> None:
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    out_dir = tmp_path / "exports"

    srt_path = export_subtitles(str(srt), str(out_dir), options=SubtitleExportOptions(format="srt"))
    vtt_path = export_subtitles(str(srt), str(out_dir), options=SubtitleExportOptions(format="vtt"))

    assert Path(srt_path).read_text(encoding="utf-8") == srt.read_text(encoding="utf-8")
    vtt = Path(vtt_path).read_text(encoding="utf-8")
    assert "WEBVTT" in vtt
    assert "00:00:01.000 --> 00:00:02.000" in vtt
