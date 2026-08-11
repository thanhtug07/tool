"""Unit tests for RenderService pure helpers (TASK-027).

No ffmpeg binary is needed: encoder picking, argument building, validation
issue detection, frame sampling math and failure classification are all pure.
"""

from __future__ import annotations

import pytest

from src.api.schemas import AudioStream, MediaMetadata, Rational
from src.core.ffmpeg import E_FFMPEG_FAILED, FFmpegError
from src.services.render_service import (
    E_RENDER_INVALID,
    DEFAULT_RENDER_CRF,
    DEFAULT_RENDER_PRESET,
    RenderError,
    _EtaEstimator,
    build_render_args,
    classify_render_failure,
    frame_region_mean,
    pick_video_encoder,
    render_validation_issues,
    subtitle_filter_arg,
)


def _metadata(
    *,
    width: int = 320,
    height: int = 240,
    fps: tuple[int, int] = (25, 1),
    codec: str = "h264",
    duration: float = 2.0,
    audio: tuple[int, int] | None = (2, 44100),
) -> MediaMetadata:
    return MediaMetadata(
        schema_version=1,
        duration=duration,
        width=width,
        height=height,
        fps=Rational(numerator=fps[0], denominator=fps[1]),
        codec=codec,
        bitrate=None,
        rotation=0,
        format="mp4",
        aspect_ratio="4:3",
        video_streams=[],
        audio_streams=(
            [
                AudioStream(
                    index=0,
                    codec="aac",
                    channels=audio[0],
                    sample_rate=audio[1],
                    bitrate=128000,
                    duration=duration,
                    language=None,
                )
            ]
            if audio
            else []
        ),
        subtitle_streams=[],
    )


class TestPickVideoEncoder:
    def test_prefers_nvenc_over_qsv_over_amf(self) -> None:
        available = ("h264_qsv", "h264_amf")
        assert pick_video_encoder(available, None) == "h264_qsv"
        assert pick_video_encoder(("h264_amf",), None) == "h264_amf"
        assert pick_video_encoder((), None) == "libx264"

    def test_full_matrix(self) -> None:
        available = ("h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf")
        assert pick_video_encoder(available, None) == "h264_nvenc"

    def test_requested_encoder_is_honoured(self) -> None:
        available = ("h264_nvenc", "libx264")
        assert pick_video_encoder(available, "libx264") == "libx264"
        assert pick_video_encoder(("libx265",), "libx265") == "libx265"

    def test_requested_encoder_is_validated(self) -> None:
        with pytest.raises(RenderError) as exc_info:
            pick_video_encoder(("libx264",), "h264_nvenc")
        assert exc_info.value.code == E_RENDER_INVALID


class TestBuildRenderArgs:
    def test_software_encoder_gets_preset_and_crf(self) -> None:
        args = build_render_args("in.mp4", "out.mp4", encoder="libx264")
        assert args[:4] == ["-y", "-nostdin", "-i", "in.mp4"]
        assert args[-1] == "out.mp4"
        assert "-preset" in args
        assert args[args.index("-preset") + 1] == DEFAULT_RENDER_PRESET
        assert "-crf" in args
        assert args[args.index("-crf") + 1] == str(DEFAULT_RENDER_CRF)

    def test_hardware_encoder_omits_preset_and_crf(self) -> None:
        args = build_render_args("in.mp4", "out.mp4", encoder="h264_nvenc")
        assert "-preset" not in args
        assert "-crf" not in args
        assert args[args.index("-c:v") + 1] == "h264_nvenc"

    def test_subtitle_filter_is_injected(self) -> None:
        args = build_render_args("in.mp4", "out.mp4", encoder="libx264", subtitle_arg="ass=sub.ass")
        assert args[args.index("-vf") + 1] == "ass=sub.ass"

    def test_audio_is_mapped_and_preserved(self) -> None:
        args = build_render_args("in.mp4", "out.mp4", encoder="libx264")
        assert args[args.index("-map") + 1] == "0:v:0"
        assert "-c:a" in args
        assert args[args.index("-c:a") + 1] == "copy"

    def test_custom_audio_codec(self) -> None:
        args = build_render_args("in.mkv", "out.mkv", encoder="libx264", audio_codec="aac")
        assert args[args.index("-c:a") + 1] == "aac"


class TestSubtitleFilterArg:
    def test_ass_uses_ass_filter(self) -> None:
        assert subtitle_filter_arg("C:/tmp/sub title.ass") == "ass=sub title.ass"

    def test_srt_uses_subtitles_filter(self) -> None:
        assert subtitle_filter_arg("C:/tmp/sub.srt") == "subtitles=sub.srt"


class TestRenderValidationIssues:
    def test_identical_metadata_is_clean(self) -> None:
        source = _metadata()
        assert render_validation_issues(source, _metadata()) == []

    def test_resolution_change_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(width=640, height=480))
        assert any("resolution" in issue for issue in issues)

    def test_fps_change_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(fps=(30, 1)))
        assert any("fps" in issue for issue in issues)

    def test_duration_drift_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(duration=5.0))
        assert any("duration" in issue for issue in issues)

    def test_unknown_output_codec_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(codec="flv"))
        assert any("codec" in issue for issue in issues)

    def test_dropped_audio_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(audio=None))
        assert any("audio" in issue for issue in issues)

    def test_audio_channel_change_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(audio=(6, 44100)))
        assert any("channels" in issue for issue in issues)

    def test_audio_sample_rate_change_is_flagged(self) -> None:
        source = _metadata()
        issues = render_validation_issues(source, _metadata(audio=(2, 48000)))
        assert any("sample rate" in issue for issue in issues)


class TestFrameRegionMean:
    def test_uniform_frame(self) -> None:
        frame = bytes([100]) * (8 * 8 * 3)
        mean = frame_region_mean(frame, 8, 8, (0.0, 0.0, 1.0, 1.0))
        assert mean == pytest.approx(100.0)

    def test_half_region_sums_right_half(self) -> None:
        width, height = 8, 8
        frame = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                idx = (y * width + x) * 3
                value = 255 if x >= 4 else 0
                frame[idx : idx + 3] = bytes([value, value, value])
        mean = frame_region_mean(bytes(frame), width, height, (0.5, 0.0, 1.0, 1.0))
        assert mean == pytest.approx(255.0)

    def test_too_small_frame_is_safe(self) -> None:
        assert frame_region_mean(b"\x00", 8, 8, (0.0, 0.0, 1.0, 1.0)) == 0.0


class TestClassifyRenderFailure:
    def test_missing_file(self) -> None:
        assert "could not be opened" in classify_render_failure("No such file or directory")

    def test_corrupted_input(self) -> None:
        assert "corrupted" in classify_render_failure("Invalid data found when processing input")

    def test_unknown_encoder(self) -> None:
        assert "encoder" in classify_render_failure("Unknown encoder 'h264_nvenc'")

    def test_generic_failure_never_embeds_paths(self) -> None:
        message = classify_render_failure("Failed to open D:\\secret\\clip.mp4: bad option")
        assert "D:\\secret" not in message
        assert "failed" in message.lower()

    def test_wraps_path_validation_error(self) -> None:
        with pytest.raises(FFmpegError) as exc_info:
            build_render_args("in;rm -rf /", "out.mp4", encoder="libx264")
        assert exc_info.value.code == E_FFMPEG_FAILED


class TestEtaEstimator:
    def test_estimates_eta_and_speed(self) -> None:
        estimator = _EtaEstimator()
        assert estimator.estimate(1.0, 1_000_000, 10.0) == (None, None)
        eta, speed = estimator.estimate(2.0, 2_000_000, 10.0)
        assert speed == pytest.approx(1.0)
        assert eta == pytest.approx(8.0)

    def test_no_movement_returns_none(self) -> None:
        estimator = _EtaEstimator()
        estimator.estimate(1.0, 0, 10.0)
        assert estimator.estimate(2.0, 0, 10.0) == (None, None)
