"""Unit tests for the safe FFmpeg helpers (TASK-012).

Pure-function tests never need a real ffmpeg binary; subprocess cancellation /
timeout semantics are exercised against ``sys.executable`` like ``test_job.py``.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from src.core.ffmpeg import (
    E_FFMPEG_FAILED,
    E_FFMPEG_NOT_FOUND,
    FFmpegError,
    build_extract_audio_args,
    classify_failure,
    out_time_seconds,
    parse_progress_line,
    progress_fraction,
    resolve_ffmpeg,
    run_ffmpeg,
    validate_input_path,
)
from src.core.job import CancelledError, CancellationToken, ProcessTimeoutError


class TestValidateInputPath:
    @pytest.mark.parametrize(
        "bad",
        [
            ";",
            "|",
            "&",
            "\n",
            "\0",
            "D:\\a;b.mp4",
            "D:\\x&y.mp4",
            "in | cmd",
        ],
    )
    def test_rejects_shell_metacharacters(self, bad: str) -> None:
        with pytest.raises(FFmpegError) as excinfo:
            validate_input_path(bad)
        assert excinfo.value.code == E_FFMPEG_FAILED

    def test_rejects_empty(self) -> None:
        with pytest.raises(FFmpegError):
            validate_input_path("")
        with pytest.raises(FFmpegError):
            validate_input_path("   ")

    def test_accepts_normal_and_unicode_paths(self) -> None:
        assert validate_input_path("D:\\Phim\\Việt Nam\\Đặc vụ.mp4") == (
            "D:\\Phim\\Việt Nam\\Đặc vụ.mp4"
        )
        assert validate_input_path("video.mp4") == "video.mp4"


class TestBuildExtractAudioArgs:
    def test_contains_canonical_spec_in_order(self) -> None:
        args = build_extract_audio_args("in.mp4", "out.wav")
        assert args == [
            "-y",
            "-nostdin",
            "-i",
            "in.mp4",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-progress",
            "pipe:1",
            "-nostats",
            "-loglevel",
            "error",
            "out.wav",
        ]

    def test_custom_spec_is_honoured(self) -> None:
        args = build_extract_audio_args("i", "o", sample_rate=48000, channels=2, codec="pcm_s24le")
        assert args[args.index("-ar") + 1] == "48000"
        assert args[args.index("-ac") + 1] == "2"
        assert args[args.index("-c:a") + 1] == "pcm_s24le"

    def test_rejects_injection_via_args_builder(self) -> None:
        with pytest.raises(FFmpegError):
            build_extract_audio_args("in;rm -rf /", "out.wav")
        with pytest.raises(FFmpegError):
            build_extract_audio_args("in.mp4", "out&evil.wav")


class TestProgressParsing:
    def test_parses_key_value_lines(self) -> None:
        assert parse_progress_line("out_time_us=2020125") == {"out_time_us": "2020125"}
        assert parse_progress_line("progress=continue") == {"progress": "continue"}
        assert parse_progress_line("  speed= 295x  ") == {"speed": " 295x"}

    def test_non_progress_lines_are_none(self) -> None:
        assert parse_progress_line("") is None
        assert parse_progress_line("   ") is None
        assert parse_progress_line("a line without equals") is None

    def test_out_time_seconds(self) -> None:
        assert out_time_seconds({"out_time_us": "2020125"}) == pytest.approx(2.020125)
        assert out_time_seconds({"out_time_ms": "500"}) == pytest.approx(0.5)
        assert out_time_seconds({"progress": "end"}) is None
        assert out_time_seconds({"out_time_us": "nope"}) is None

    def test_progress_fraction_clamps(self) -> None:
        assert progress_fraction(0, 2.0) == 0.0
        assert progress_fraction(1_000_000, 2.0) == pytest.approx(0.5)
        assert progress_fraction(9_000_000, 2.0) == 1.0
        assert progress_fraction(1_000_000, 0) == 0.0


class TestClassifyFailure:
    def test_no_audio_marker_is_clear(self) -> None:
        message = classify_failure("[out#0/wav] Output file does not contain any stream")
        assert "no audio" in message.lower()

    def test_generic_failure(self) -> None:
        message = classify_failure("some random error: codec not found")
        assert "failed" in message.lower()

    def test_error_message_never_embeds_paths(self) -> None:
        message = classify_failure("Cannot open D:\\secret\\video.mp4: No such file")
        assert "D:\\secret" not in message


class TestResolveFfmpeg:
    def test_defaults_to_bare_ffmpeg(self, monkeypatch) -> None:
        monkeypatch.delenv("FFMPEG_BIN", raising=False)
        assert resolve_ffmpeg() == "ffmpeg"

    def test_env_override_must_be_allowlisted(self, monkeypatch, tmp_path) -> None:
        exe = tmp_path / "evil.exe"
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("FFMPEG_BIN", str(exe))
        with pytest.raises(FFmpegError) as excinfo:
            resolve_ffmpeg()
        assert excinfo.value.code == E_FFMPEG_NOT_FOUND

    def test_existing_allowlisted_path_is_accepted(self, monkeypatch, tmp_path) -> None:
        exe = tmp_path / "ffmpeg.exe"
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("FFMPEG_BIN", str(exe))
        assert resolve_ffmpeg() == str(exe)


class TestRunFfmpegCancellation:
    def test_cancel_before_start_raises_immediately(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], cancel=token)

    def test_cancel_mid_run_kills_process(self) -> None:
        token = CancellationToken()

        def _cancel_later() -> None:
            time.sleep(0.3)
            token.cancel()

        threading.Thread(target=_cancel_later, daemon=True).start()
        started = time.monotonic()
        with pytest.raises(CancelledError):
            run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], cancel=token)
        assert time.monotonic() - started < 20

    def test_timeout_kills_process(self) -> None:
        with pytest.raises(ProcessTimeoutError):
            run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)

    def test_clean_run_reports_zero(self) -> None:
        result = run_ffmpeg([sys.executable, "-c", "print('x')"])
        assert result.returncode == 0
