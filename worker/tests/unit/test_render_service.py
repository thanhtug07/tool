"""Unit tests for RenderService pure helpers (TASK-027).

No ffmpeg binary is needed: encoder picking, argument building, validation
issue detection, frame sampling math and failure classification are all pure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.api.schemas import AudioStream, MediaMetadata, Rational, VideoStream
from src.core.ffmpeg import E_FFMPEG_FAILED, FFmpegError
from src.services.render_service import (
    E_RENDER_INVALID,
    DEFAULT_RENDER_CRF,
    DEFAULT_RENDER_PRESET,
    E_EXPORT_INVALID,
    E_PERMISSION_DENIED,
    ImageWatermark,
    RenderError,
    SubtitleExportOptions,
    TextWatermark,
    WatermarkConfig,
    WM_POSITIONS,
    _EtaEstimator,
    build_drawtext_filter,
    build_filter_graph,
    build_qc_report,
    build_render_args,
    classify_render_failure,
    escape_drawtext,
    export_subtitles,
    export_video,
    frame_region_mean,
    pick_video_encoder,
    prepare_watermark,
    render_validation_issues,
    srt_to_vtt,
    subtitle_filter_arg,
    validate_watermark,
    vtt_to_srt,
    watermark_fingerprint,
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
        video_streams=[
            VideoStream(
                index=0,
                codec=codec,
                profile="Main",
                width=width,
                height=height,
                fps=Rational(numerator=fps[0], denominator=fps[1]),
                pixel_format="yuv420p",
                aspect_ratio="4:3",
                bitrate=500000,
                duration=duration,
            )
        ],
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


class TestEscapeDrawtext:
    def test_special_chars_are_backslash_escaped(self) -> None:
        text = "A: B, C [x] 50% {y}; $ \\z"
        escaped = escape_drawtext(text)
        assert escaped == "'A\\: B\\, C \\[x\\] 50\\% \\{y\\}\\; $ \\\\z'"

    def test_plain_text_is_just_quoted(self) -> None:
        assert escape_drawtext("hello world") == "'hello world'"

    def test_empty_string_is_quoted_empty(self) -> None:
        assert escape_drawtext("") == "''"

    def test_single_quote_is_rejected(self) -> None:
        with pytest.raises(RenderError) as exc_info:
            escape_drawtext("don't panic")
        assert exc_info.value.code == E_RENDER_INVALID

    def test_non_string_is_rejected(self) -> None:
        with pytest.raises(RenderError) as exc_info:
            escape_drawtext(123)  # type: ignore[arg-type]
        assert exc_info.value.code == E_RENDER_INVALID


class TestBuildDrawtextFilter:
    def test_inlines_escaped_text_without_font(self) -> None:
        wm = TextWatermark(text="A: B, C [x] 50% {y};", position="top-left", margin=10)
        out = build_drawtext_filter(wm)
        assert out.startswith("drawtext=")
        assert "text='A\\: B\\, C \\[x\\] 50\\% \\{y\\}\\;'" in out
        assert "expansion=none" in out
        assert "fontsize=48" in out
        assert "fontcolor=#FFFFFFFF" in out
        assert "x=10" in out
        assert "y=10" in out

    def test_anchored_positions_use_filter_variables(self) -> None:
        wm = TextWatermark(text="wm", position="center")
        out = build_drawtext_filter(wm)
        assert "x=(w-text_w)/2" in out
        assert "y=(h-text_h)/2" in out

    def test_custom_position_uses_explicit_xy(self) -> None:
        wm = TextWatermark(text="wm", position="custom", x=42, y=17)
        out = build_drawtext_filter(wm)
        assert "x=42" in out
        assert "y=17" in out

    def test_textfile_route_when_single_quote_present(self) -> None:
        wm = TextWatermark(text="it's mine", position="bottom-right")
        out = build_drawtext_filter(wm, textfile="wm_text.txt")
        assert "textfile=wm_text.txt" in out
        assert "text=" + "'" not in out

    def test_fontfile_overrides_font_for_no_path_escaping(self) -> None:
        wm = TextWatermark(text="wm", font_file="C:/Some Dir/a:rial.ttf")
        assert "fontfile=arial.ttf" not in build_drawtext_filter(wm)  # path never in graph
        out = build_drawtext_filter(wm, fontfile="wm_font.bin")
        assert "fontfile=wm_font.bin" in out

    def test_opacity_below_one_adds_alpha(self) -> None:
        wm = TextWatermark(text="wm", opacity=0.5)
        assert "alpha=0.500" in build_drawtext_filter(wm)

    def test_rotation_is_emitted(self) -> None:
        wm = TextWatermark(text="wm", rotation=1.5)
        assert "rotation=1.5000" in build_drawtext_filter(wm)


class TestValidateWatermark:
    def test_none_is_fine(self) -> None:
        validate_watermark(None)
        validate_watermark(WatermarkConfig())

    def test_unknown_position_is_rejected(self) -> None:
        with pytest.raises(RenderError) as exc_info:
            validate_watermark(WatermarkConfig(text=TextWatermark(text="x", position="middle")))
        assert exc_info.value.code == E_RENDER_INVALID

    def test_opacity_out_of_range_is_rejected(self) -> None:
        with pytest.raises(RenderError):
            validate_watermark(WatermarkConfig(text=TextWatermark(text="x", opacity=1.5)))
        with pytest.raises(RenderError):
            validate_watermark(WatermarkConfig(text=TextWatermark(text="x", opacity=-0.1)))

    def test_empty_text_is_rejected(self) -> None:
        with pytest.raises(RenderError):
            validate_watermark(WatermarkConfig(text=TextWatermark(text="")))

    def test_negative_custom_position_is_rejected(self) -> None:
        with pytest.raises(RenderError):
            validate_watermark(
                WatermarkConfig(text=TextWatermark(text="x", position="custom", x=-1, y=0))
            )

    def test_missing_image_is_rejected(self) -> None:
        with pytest.raises(RenderError):
            validate_watermark(
                WatermarkConfig(image=ImageWatermark(image_path="C:/no/such.png"))
            )

    def test_non_image_extension_is_rejected(self, tmp_path) -> None:
        bogus = tmp_path / "wm.txt"
        bogus.write_text("not an image", encoding="utf-8")
        with pytest.raises(RenderError):
            validate_watermark(
                WatermarkConfig(image=ImageWatermark(image_path=str(bogus)))
            )


class TestBuildFilterGraph:
    def test_nothing_to_filter_returns_none(self) -> None:
        assert build_filter_graph() is None

    def test_text_only_stays_single_vf(self) -> None:
        wm = TextWatermark(text="wm", position="top")
        graph = build_filter_graph(text_watermark=wm)
        assert graph is not None
        assert graph.option == "-vf"
        assert graph.extra_input is None
        assert graph.value.startswith("drawtext=")

    def test_subtitle_then_text_chained_in_vf(self) -> None:
        wm = TextWatermark(text="wm")
        graph = build_filter_graph(subtitle_arg="ass=sub.ass", text_watermark=wm)
        assert graph is not None
        assert graph.value == "ass=sub.ass,drawtext=" + build_drawtext_filter(wm).split("=", 1)[1]

    def test_image_watermark_uses_filter_complex_with_second_input(self) -> None:
        wm = ImageWatermark(image_path="C:/wm.png", position="bottom-left", margin=8)
        graph = build_filter_graph(image_watermark=wm, image_input="C:/wm.png")
        assert graph is not None
        assert graph.option == "-filter_complex"
        assert graph.extra_input == "C:/wm.png"
        assert "[0:v]" in graph.value
        assert "[1:v]" in graph.value
        assert "[vbase][wmimg]overlay=" in graph.value
        assert "[vout]" in graph.value

    def test_image_scale_and_opacity_prep(self) -> None:
        wm = ImageWatermark(image_path="C:/wm.png", width=120, opacity=0.4)
        graph = build_filter_graph(image_watermark=wm, image_input="C:/wm.png")
        assert graph is not None
        assert "scale=120:-2:flags=lanczos" in graph.value
        assert "format=rgba,colorchannelmixer=aa=0.400" in graph.value

    def test_text_and_image_combined_chain(self) -> None:
        text = TextWatermark(text="wm", position="top-left")
        img = ImageWatermark(image_path="C:/wm.png")
        graph = build_filter_graph(text_watermark=text, image_watermark=img, image_input="C:/wm.png")
        assert graph is not None
        assert graph.value.startswith("[0:v]drawtext=")
        assert "[vbase][wmimg]overlay=" in graph.value


class TestBuildRenderArgsWithFilterGraph:
    def test_filter_graph_vf_maps_primary_video(self) -> None:
        graph = build_filter_graph(text_watermark=TextWatermark(text="wm"))
        args = build_render_args("in.mp4", "out.mp4", encoder="libx264", filter_graph=graph)
        assert args[args.index("-vf") + 1] == graph.value
        assert args[args.index("-map") + 1] == "0:v:0"

    def test_filter_complex_maps_to_vout_label(self) -> None:
        graph = build_filter_graph(
            image_watermark=ImageWatermark(image_path="C:/wm.png"), image_input="C:/wm.png"
        )
        args = build_render_args("in.mp4", "out.mp4", encoder="libx264", filter_graph=graph)
        assert args[args.index("-filter_complex") + 1] == graph.value
        assert "-i" in args
        assert args[args.index("-map") + 1] == "[vout]"


class TestPrepareWatermark:
    def test_none_returns_nones(self, tmp_path) -> None:
        assert prepare_watermark(tmp_path, None) == (None, None, None)

    def test_copies_image_under_generated_name(self, tmp_path) -> None:
        src = tmp_path / "logo.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        textfile, fontfile, image_input = prepare_watermark(
            tmp_path, WatermarkConfig(image=ImageWatermark(image_path=str(src)))
        )
        assert textfile is None
        assert fontfile is None
        assert image_input is not None
        assert Path(image_input).name == "wm_image.png"
        assert Path(image_input).is_file()

    def test_text_with_quote_writes_payload_file(self, tmp_path) -> None:
        wm = WatermarkConfig(text=TextWatermark(text="don't panic"))
        textfile, _, _ = prepare_watermark(tmp_path, wm)
        assert textfile == "wm_text.txt"
        assert (tmp_path / "wm_text.txt").read_text(encoding="utf-8") == "don't panic"

    def test_copies_font_file(self, tmp_path) -> None:
        font = tmp_path / "Fancy.ttf"
        font.write_bytes(b"0" * 128)
        wm = WatermarkConfig(text=TextWatermark(text="wm", font_file=str(font)))
        _, fontfile, _ = prepare_watermark(tmp_path, wm)
        assert fontfile == "wm_font.bin"


class TestWatermarkFingerprint:
    def test_none_maps_to_none_string(self) -> None:
        assert watermark_fingerprint(None) == "none"
        assert watermark_fingerprint(WatermarkConfig()) == "none"

    def test_text_changes_fingerprint(self) -> None:
        a = watermark_fingerprint(WatermarkConfig(text=TextWatermark(text="hello", position="top")))
        b = watermark_fingerprint(WatermarkConfig(text=TextWatermark(text="hello", position="bottom")))
        assert a != b

    def test_image_content_hashes_fingerprint(self, tmp_path) -> None:
        src = tmp_path / "logo.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"A" * 64)
        a = watermark_fingerprint(WatermarkConfig(image=ImageWatermark(image_path=str(src))))
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"B" * 64)
        b = watermark_fingerprint(WatermarkConfig(image=ImageWatermark(image_path=str(src))))
        assert a != b

    def test_all_named_positions_are_valid(self, tmp_path) -> None:
        src = tmp_path / "logo.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"A" * 64)
        assert len(WM_POSITIONS) == 9
        for position in WM_POSITIONS:
            validate_watermark(
                WatermarkConfig(image=ImageWatermark(image_path=str(src), position=position))
            )

    def test_changing_watermark_changes_render_cache_key(self, tmp_path) -> None:
        from src.services.cache import render_key

        base = "video_sha256"
        key_none = render_key(base, "styleA", watermark_fingerprint(None), "libx264", "fast")
        key_text = render_key(
            base,
            "styleA",
            watermark_fingerprint(
                WatermarkConfig(text=TextWatermark(text="hello", position="top-left"))
            ),
            "libx264",
            "fast",
        )
        key_text_bottom = render_key(
            base,
            "styleA",
            watermark_fingerprint(
                WatermarkConfig(text=TextWatermark(text="hello", position="bottom"))
            ),
            "libx264",
            "fast",
        )
        assert key_none != key_text
        assert key_text != key_text_bottom


class TestSrtVttConversion:
    def test_srt_to_vtt_prepends_header_and_dots(self) -> None:
        srt = "1\n00:00:01,000 --> 00:00:02,500\nhello world\n\n2\n00:00:05,000 --> 00:00:06,000\nsecond\n"
        vtt = srt_to_vtt(srt)
        assert vtt.startswith("WEBVTT\n\n")
        assert "00:00:01.000 --> 00:00:02.500" in vtt
        assert "00:00:05.000 --> 00:00:06.000" in vtt
        assert "hello world" in vtt
        assert "second" in vtt

    def test_vtt_to_srt_adds_indices_and_commas(self) -> None:
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nhello\n\n00:00:05.000 --> 00:00:06.000\nsecond\n"
        srt = vtt_to_srt(vtt)
        assert srt == "1\n00:00:01,000 --> 00:00:02,500\nhello\n\n2\n00:00:05,000 --> 00:00:06,000\nsecond\n"

    def test_roundtrip_preserves_timing_and_text(self) -> None:
        srt = "1\n00:00:01,000 --> 00:00:02,500\nhello\n\n2\n00:00:05,000 --> 00:00:06,000\nsecond\n"
        assert vtt_to_srt(srt_to_vtt(srt)) == srt


class TestBuildQcReport:
    def test_identical_media_passes(self) -> None:
        meta = _metadata()
        report = build_qc_report(meta, meta)
        assert report.passed is True
        assert report.issues == ()

    def test_duration_drift_fails(self) -> None:
        source = _metadata(duration=10.0)
        output = _metadata(duration=12.5)
        report = build_qc_report(source, output)
        assert report.passed is False
        assert any("duration" in issue for issue in report.issues)

    def test_resolution_change_fails(self) -> None:
        source = _metadata(width=320, height=240)
        output = _metadata(width=640, height=480)
        report = build_qc_report(source, output)
        assert report.passed is False
        assert any("resolution" in issue for issue in report.issues)

    def test_audio_dropped_fails(self) -> None:
        source = _metadata(audio=(2, 44100))
        output = _metadata(audio=None)
        report = build_qc_report(source, output)
        assert report.passed is False
        assert any("audio" in issue for issue in report.issues)

    def test_no_video_stream_fails(self) -> None:
        source = _metadata()
        output = _metadata(width=0, height=0, fps=(0, 1))
        output = MediaMetadata(
            schema_version=1,
            duration=output.duration,
            width=None,
            height=None,
            fps=output.fps,
            codec=None,
            bitrate=None,
            rotation=0,
            format="mp4",
            aspect_ratio=None,
            video_streams=[],
            audio_streams=[],
            subtitle_streams=[],
        )
        report = build_qc_report(source, output)
        assert report.passed is False
        assert any("video" in issue for issue in report.issues)


class TestExportVideo:
    def test_copy_and_qc_pass(self, tmp_path) -> None:
        source = tmp_path / "rendered.mp4"
        source.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"B" * 4096)
        out_dir = tmp_path / "out"
        from src.services import render_service

        real_probe = render_service.probe
        try:
            render_service.probe = lambda path: _metadata(duration=2.0, width=320, height=240)
            result = export_video(str(source), str(out_dir), name="final")
        finally:
            render_service.probe = real_probe
        assert os.path.isfile(result.path)
        assert result.path == os.path.join(str(out_dir), "final.mp4")
        assert result.qc.passed is True

    def test_missing_source_is_invalid(self, tmp_path) -> None:
        with pytest.raises(RenderError) as exc_info:
            export_video(str(tmp_path / "nope.mp4"), str(tmp_path))
        assert exc_info.value.code == E_EXPORT_INVALID

    def test_automatic_suffix_on_collision(self, tmp_path) -> None:
        source = tmp_path / "rendered.mp4"
        source.write_bytes(b"abc" * 1024)
        out_dir = tmp_path / "out"
        from src.services import render_service

        real_probe = render_service.probe
        try:
            render_service.probe = lambda path: _metadata(duration=2.0)
            first = export_video(str(source), str(out_dir), name="clip")
            second = export_video(str(source), str(out_dir), name="clip")
        finally:
            render_service.probe = real_probe
        assert first.path.endswith("clip.mp4")
        assert second.path.endswith("clip (1).mp4")
        assert os.path.isfile(second.path)

    def test_export_to_unwritable_dir_raises_permission(self, tmp_path) -> None:
        source = tmp_path / "rendered.mp4"
        source.write_bytes(b"abc" * 1024)
        out_dir = tmp_path / "locked"
        out_dir.mkdir()
        if os.name != "nt":
            os.chmod(out_dir, 0o500)
            try:
                with pytest.raises(RenderError) as exc_info:
                    export_video(str(source), str(out_dir), run_qc=False)
                assert exc_info.value.code == E_PERMISSION_DENIED
            finally:
                os.chmod(out_dir, 0o700)


class TestExportSubtitles:
    def test_copies_srt_passthrough(self, tmp_path) -> None:
        src = tmp_path / "subtitle.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        out_dir = tmp_path / "out"
        path = export_subtitles(str(src), str(out_dir), options=SubtitleExportOptions(format="srt"))
        assert os.path.isfile(path)
        assert Path(path).name == "subtitle.srt"
        assert Path(path).read_text(encoding="utf-8") == src.read_text(encoding="utf-8")

    def test_converts_srt_to_vtt_on_export(self, tmp_path) -> None:
        src = tmp_path / "subtitle.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        out_dir = tmp_path / "out"
        path = export_subtitles(str(src), str(out_dir), options=SubtitleExportOptions(format="vtt"))
        assert Path(path).name == "subtitle.vtt"
        assert "WEBVTT" in Path(path).read_text(encoding="utf-8")
        assert "00:00:01.000 --> 00:00:02.000" in Path(path).read_text(encoding="utf-8")

    def test_converts_vtt_to_srt_on_export(self, tmp_path) -> None:
        src = tmp_path / "subtitle.vtt"
        src.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n", encoding="utf-8")
        out_dir = tmp_path / "out"
        path = export_subtitles(str(src), str(out_dir), options=SubtitleExportOptions(format="srt"))
        assert Path(path).name == "subtitle.srt"
        assert Path(path).read_text(encoding="utf-8").startswith("1\n00:00:01,000")

    def test_ass_cannot_convert(self, tmp_path) -> None:
        src = tmp_path / "subtitle.ass"
        src.write_text("[Script Info]\n", encoding="utf-8")
        with pytest.raises(RenderError) as exc_info:
            export_subtitles(str(src), str(tmp_path), options=SubtitleExportOptions(format="srt"))
        assert exc_info.value.code == E_EXPORT_INVALID

    def test_missing_source_is_invalid(self, tmp_path) -> None:
        with pytest.raises(RenderError) as exc_info:
            export_subtitles(str(tmp_path / "nope.srt"), str(tmp_path))
        assert exc_info.value.code == E_EXPORT_INVALID

    def test_automatic_suffix_on_collision(self, tmp_path) -> None:
        src = tmp_path / "subtitle.srt"
        src.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
        out_dir = tmp_path / "out"
        first = export_subtitles(str(src), str(out_dir))
        second = export_subtitles(str(src), str(out_dir))
        assert first.endswith("subtitle.srt")
        assert second.endswith("subtitle (1).srt")
