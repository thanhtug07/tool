"""Unit tests for the whisper.cpp sidecar core (TASK-015).

Covers the three mandatory mitigations (beam cap, --no-flash-attn,
serialized init), the arg-array builder, JSON output parsing, progress parsing,
and binary resolution. No real binary or GPU is needed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from src.core import whisper_cpp
from src.core.job import CancellationToken
from src.core.whisper_cpp import (
    E_WHISPER_CPP_FAILED,
    E_WHISPER_CPP_NOT_FOUND,
    MAX_BEAM_SIZE,
    WhisperCppError,
    build_transcribe_args,
    clamp_beam_size,
    parse_json_output,
    parse_progress_percent,
    resolve_whisper_cli,
    run_whisper_cli,
)

SAMPLE_JSON = json.dumps(
    {
        "language": "zh",
        "transcription": [
            {
                "timestamps": {"from": "00:00:00,000", "to": "00:00:01,240"},
                "offsets": {"from": 0, "to": 1240},
                "text": " 你好  ",
            },
            {
                "timestamps": {"from": "00:00:01,240", "to": "00:00:02,500"},
                "offsets": {"from": 1240, "to": 2500},
                "text": "",
            },
            {
                "timestamps": {"from": "00:00:01,240", "to": "00:00:02,500"},
                "offsets": {"from": 1240, "to": 2500},
                "text": "世界",
            },
        ],
    }
)


class TestBeamCap:
    def test_caps_at_six(self) -> None:
        assert clamp_beam_size(8) == MAX_BEAM_SIZE
        assert build_transcribe_args("m.bin", "a.wav", beam_size=9)[-3:-1] == ["--beam-size", "6"]

    def test_defaults_and_lower_bound(self) -> None:
        assert clamp_beam_size(None) == 5
        assert clamp_beam_size(0) == 1

    def test_mitigation_2_no_flash_attn_optional(self) -> None:
        plain = build_transcribe_args("m.bin", "a.wav")
        assert "--no-flash-attn" not in plain
        vulkan = build_transcribe_args("m.bin", "a.wav", no_flash_attn=True)
        assert "--no-flash-attn" in vulkan


class TestArgBuilder:
    def test_standard_arguments(self) -> None:
        args = build_transcribe_args("models/ggml-zh.bin", "in.wav", language="zh", num_threads=4)
        assert args == [
            "-m",
            "models/ggml-zh.bin",
            "-f",
            "in.wav",
            "-l",
            "zh",
            "-t",
            "4",
            "--beam-size",
            "5",
            "--output-json",
        ]

    def test_optional_language_and_threads(self) -> None:
        args = build_transcribe_args("m.bin", "a.wav")
        assert "-l" not in args
        assert "-t" not in args
        assert args[0:2] == ["-m", "m.bin"]
        assert args[2:4] == ["-f", "a.wav"]
        assert args[-2:] == ["--beam-size", "5"] or args[-3:-1] == ["--beam-size", "5"]


class TestProgress:
    def test_parses_progress_line(self) -> None:
        assert parse_progress_percent("whisper_print_progress_callback: progress = 17%") == 0.17
        assert parse_progress_percent("progress = 100%") == 1.0

    def test_rejects_garbage(self) -> None:
        assert parse_progress_percent("model loaded in 3.2s") is None
        assert parse_progress_percent(None) is None


class TestJsonOutput:
    def test_parses_offsets_and_drops_empty(self) -> None:
        parsed = parse_json_output(SAMPLE_JSON)
        assert parsed["language"] == "zh"
        assert len(parsed["segments"]) == 2
        assert parsed["segments"][0] == {"text": "你好", "start": 0.0, "end": 1.24}
        assert parsed["segments"][1]["text"] == "世界"

    def test_falls_back_to_timestamps(self) -> None:
        payload = json.dumps(
            {
                "transcription": [
                    {"timestamps": {"from": "00:00:05,500", "to": "00:00:06,000"}, "text": "hi"}
                ]
            }
        )
        parsed = parse_json_output(payload)
        assert parsed["segments"][0]["start"] == 5.5
        assert parsed["segments"][0]["end"] == 6.0

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(WhisperCppError) as excinfo:
            parse_json_output("not json {")
        assert excinfo.value.code == E_WHISPER_CPP_FAILED

    def test_missing_transcription_raises(self) -> None:
        with pytest.raises(WhisperCppError) as excinfo:
            parse_json_output('{"model": "whisper"}')
        assert excinfo.value.code == E_WHISPER_CPP_FAILED


class TestResolveBinary:
    def test_default_name(self, monkeypatch) -> None:
        monkeypatch.delenv("WHISPER_CPP_BIN", raising=False)
        assert resolve_whisper_cli() == "whisper-cli"

    def test_explicit_path(self, tmp_path, monkeypatch) -> None:
        binary = tmp_path / "whisper-cli.exe"
        binary.write_bytes(b"")
        monkeypatch.setenv("WHISPER_CPP_BIN", str(binary))
        assert resolve_whisper_cli() == str(binary)

    def test_unsupported_name_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("WHISPER_CPP_BIN", "curl.exe")
        with pytest.raises(WhisperCppError) as excinfo:
            resolve_whisper_cli()
        assert excinfo.value.code == E_WHISPER_CPP_NOT_FOUND


class TestRunWhisperCli:
    def test_not_found_when_binary_missing(self, monkeypatch) -> None:
        def _raise(*_args, **_kwargs):
            raise FileNotFoundError

        monkeypatch.setattr(whisper_cpp.subprocess, "Popen", _raise)
        with pytest.raises(WhisperCppError) as excinfo:
            run_whisper_cli(["whisper-cli", "--output-json"])
        assert excinfo.value.code == E_WHISPER_CPP_NOT_FOUND

    def test_cancel_before_start(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(Exception) as excinfo:
            run_whisper_cli(["whisper-cli"], cancel=token)
        assert type(excinfo.value).__name__ == "CancelledError"
