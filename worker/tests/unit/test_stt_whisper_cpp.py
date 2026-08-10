"""Unit tests for the whisper.cpp fallback in stt_service (TASK-015).

Exercises backend routing (auto via strategy / explicit whisper-cpp), the three
mandatory mitigations (beam cap, --no-flash-attn, serialized init), and the
error mapping — all with an injected ``whisper_cli`` seam, no binary needed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.core.job import CancellationToken
from src.core.whisper_cpp import E_WHISPER_CPP_FAILED, E_WHISPER_CPP_NOT_FOUND, WhisperCppError
from src.services import stt_service
from src.services.hardware import Strategy
from src.services.stt_service import (
    BACKEND_WHISPER_CPP,
    E_STT_FAILED,
    E_STT_NO_SPEECH,
    STTError,
    _WHISPER_INIT_LOCK,
    transcribe,
)

TRANSCRIPTION = json.dumps(
    {
        "language": "zh",
        "transcription": [
            {"offsets": {"from": 0, "to": 500}, "text": "第一段"},
            {"offsets": {"from": 500, "to": 1000}, "text": "第二段"},
        ],
    }
)


def _result(returncode=0, output_json=TRANSCRIPTION):
    return SimpleNamespace(returncode=returncode, output_json=output_json, progress=1.0)


class _FakeCli:
    """Records the args it was called with and asserts lock serialization."""

    def __init__(self, result=None, assert_locked=False, error=None):
        self.result = result
        self.assert_locked = assert_locked
        self.error = error
        self.calls = []

    def __call__(self, args, *, cancel=None, on_progress=None):
        if self.assert_locked:
            assert _WHISPER_INIT_LOCK.locked() is True
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result or _result()


def _whisper_cpp_strategy() -> Strategy:
    return Strategy(
        device="cpu",
        compute_type="int8",
        stt_backend=BACKEND_WHISPER_CPP,
        whisper_encoder="libx264",
        vulkan=True,
    )


class TestWhisperCppBackend:
    def test_explicit_backend_routes_and_builds_transcript(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli()
        result = transcribe(
            str(audio),
            project_id="proj-1",
            model_name="whisper-cpp",
            device="cpu",
            backend=BACKEND_WHISPER_CPP,
            model_path="models/ggml-zh.bin",
            whisper_cli=cli,
        )
        assert result.model_used == "whisper-cpp"
        assert result.transcript["language"] == "zh"
        assert [s["text"] for s in result.transcript["segments"]] == ["第一段", "第二段"]
        args = cli.calls[0]
        assert args[:5] == ["whisper-cli", "-m", "models/ggml-zh.bin", "-f", str(audio)]

    def test_auto_backend_routes_via_strategy(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli()
        result = transcribe(
            str(audio),
            project_id="p",
            model_name="whisper-cpp",
            backend="auto",
            strategy=_whisper_cpp_strategy(),
            model_path="m.bin",
            whisper_cli=cli,
        )
        assert result.model_used == "whisper-cpp"
        assert cli.calls  # routed to whisper-cpp

    def test_auto_backend_defaults_to_faster_whisper(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        model = SimpleNamespace(transcribe=lambda *a, **k: (iter(()), None))
        # An empty result surfaces E_STT_NO_SPEECH through the faster-whisper
        # path — NOT a whisper-cpp "missing model_path" error — proving routing.
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="large-v3",
                backend="auto",
                strategy=Strategy(
                    device="cpu",
                    compute_type="int8",
                    stt_backend="faster-whisper",
                    whisper_encoder="libx264",
                    vulkan=False,
                ),
                whisper_model=model,
            )
        assert excinfo.value.code == E_STT_NO_SPEECH


class TestMitigations:
    def test_beam_size_capped_at_six(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli()
        transcribe(
            str(audio),
            project_id="p",
            model_name="whisper-cpp",
            backend=BACKEND_WHISPER_CPP,
            model_path="m.bin",
            beam_size=9,
            whisper_cli=cli,
        )
        args = cli.calls[0]
        beam_idx = args.index("--beam-size")
        assert int(args[beam_idx + 1]) == 6

    def test_no_flash_attn_for_vulkan(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli()
        transcribe(
            str(audio),
            project_id="p",
            model_name="whisper-cpp",
            backend=BACKEND_WHISPER_CPP,
            model_path="m.bin",
            no_flash_attn=True,
            whisper_cli=cli,
        )
        assert "--no-flash-attn" in cli.calls[0]

    def test_init_serialized_behind_lock(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli(assert_locked=True)
        transcribe(
            str(audio),
            project_id="p",
            model_name="whisper-cpp",
            backend=BACKEND_WHISPER_CPP,
            model_path="m.bin",
            whisper_cli=cli,
        )


class TestWhisperCppErrors:
    def test_missing_model_path(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="whisper-cpp",
                backend=BACKEND_WHISPER_CPP,
                whisper_cli=_FakeCli(),
            )
        assert excinfo.value.code == E_STT_FAILED

    def test_missing_audio(self, tmp_path) -> None:
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(tmp_path / "nope.wav"),
                project_id="p",
                model_name="whisper-cpp",
                backend=BACKEND_WHISPER_CPP,
                model_path="m.bin",
                whisper_cli=_FakeCli(),
            )
        assert excinfo.value.code == E_STT_FAILED

    def test_nonzero_returncode(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli(result=_result(returncode=1))
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="whisper-cpp",
                backend=BACKEND_WHISPER_CPP,
                model_path="m.bin",
                whisper_cli=cli,
            )
        assert excinfo.value.code == E_STT_FAILED

    def test_runner_error_mapped_to_stt_error(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        cli = _FakeCli(error=WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "no binary"))
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="whisper-cpp",
                backend=BACKEND_WHISPER_CPP,
                model_path="m.bin",
                whisper_cli=cli,
            )
        assert excinfo.value.code == E_WHISPER_CPP_NOT_FOUND

    def test_cancel_before_start(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        token = CancellationToken()
        token.cancel()
        with pytest.raises(Exception) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="whisper-cpp",
                backend=BACKEND_WHISPER_CPP,
                model_path="m.bin",
                whisper_cli=_FakeCli(),
                cancel=token,
            )
        assert type(excinfo.value).__name__ == "CancelledError"


def test_lock_is_a_threading_lock() -> None:
    from threading import Lock

    assert isinstance(_WHISPER_INIT_LOCK, Lock)
