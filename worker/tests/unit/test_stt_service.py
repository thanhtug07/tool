"""Unit tests for STTService (TASK-013) — mock model, no AI download needed.

The WhisperModel is injected as a test double exposing the faster-whisper 1.x
contract ``transcribe(audio, ...) -> (segments, info)``, so the pipeline logic
(transcript building, VAD wiring, progress, cancel, VRAM guard) is exercised
without the `ai` marker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.services.stt_service as stt_service
from src.core.job import CancelledError, CancellationToken
from src.services.stt_service import (
    E_STT_FAILED,
    E_STT_NO_SPEECH,
    STTError,
    _is_cuda_library_error,
    build_transcript,
    guard_model_tier,
    pick_compute_type,
    resolve_device,
    transcribe,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "transcript.schema.json"


class FakeSegment:
    def __init__(self, start, end, text, language=None, avg_logprob=-0.5):
        self.start = start
        self.end = end
        self.text = text
        self.language = language
        self.avg_logprob = avg_logprob


class FakeInfo:
    def __init__(self, language="zh", duration=2.0):
        self.language = language
        self.duration = duration


class FakeModel:
    def __init__(self, segments, info):
        self.segments = segments
        self.info = info
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return (iter(self.segments), self.info)


def _transcript_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text("utf-8"))


def test_build_transcript_produces_canonical_shape() -> None:
    segments = [
        FakeSegment(0.0, 0.8, "你好", language="zh", avg_logprob=-0.3),
        FakeSegment(0.8, 1.6, "世界", language="zh", avg_logprob=-0.6),
    ]
    doc = build_transcript(
        segments,
        project_id="proj",
        model_name="large-v3",
        language_override=None,
        detected_language="zh",
    )
    assert doc["schema_version"] == 1
    assert doc["project_id"] == "proj"
    assert doc["language"] == "zh"
    assert doc["model"] == "large-v3"
    assert len(doc["segments"]) == 2
    first = doc["segments"][0]
    assert first["id"] == "seg_0"
    assert first["idx"] == 0
    assert first["start"] == 0.0
    assert first["end"] == 0.8
    assert first["text"] == "你好"
    assert first["language"] == "zh"
    assert 0.0 <= first["confidence"] <= 1.0


def test_build_transcript_validates_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    segments = [
        FakeSegment(0.0, 0.5, "one"),
        FakeSegment(0.5, 1.0, "two"),
        FakeSegment(1.0, 1.5, "three"),
    ]
    doc = build_transcript(
        segments,
        project_id="proj-1",
        model_name="turbo",
        language_override="zh",
        detected_language=None,
    )
    jsonschema.validate(doc, _transcript_schema())


def test_build_transcript_drops_empty_segments() -> None:
    segments = [FakeSegment(0.0, 1.0, "   "), FakeSegment(1.0, 2.0, "hello")]
    doc = build_transcript(
        segments,
        project_id="p",
        model_name="m",
        language_override=None,
        detected_language="zh",
    )
    assert len(doc["segments"]) == 1
    assert doc["segments"][0]["text"] == "hello"


def test_build_transcript_language_fallback_is_never_empty() -> None:
    segments = [FakeSegment(0.0, 1.0, "hi", language=None)]
    doc = build_transcript(
        segments,
        project_id="p",
        model_name="m",
        language_override=None,
        detected_language=None,
    )
    assert doc["language"] == "und"
    assert doc["segments"][0]["language"] == "und"


def test_build_transcript_no_speech_raises() -> None:
    with pytest.raises(STTError) as excinfo:
        build_transcript(
            [],
            project_id="p",
            model_name="m",
            language_override=None,
            detected_language="zh",
        )
    assert excinfo.value.code == E_STT_NO_SPEECH


def test_build_transcript_cancel_between_segments() -> None:
    token = CancellationToken()

    def _gen():
        yield FakeSegment(0.0, 1.0, "first")
        token.cancel()
        yield FakeSegment(1.0, 2.0, "second")

    with pytest.raises(CancelledError):
        build_transcript(
            _gen(),
            project_id="p",
            model_name="m",
            language_override=None,
            detected_language="zh",
            cancel=token,
        )


def test_build_transcript_progress_reports_ratios() -> None:
    segments = [FakeSegment(0.0, 0.5, "a"), FakeSegment(0.5, 1.0, "b")]
    ratios: list[float] = []
    build_transcript(
        segments,
        project_id="p",
        model_name="m",
        language_override=None,
        detected_language="zh",
        total_duration_seconds=1.0,
        on_progress=ratios.append,
    )
    assert ratios == [0.5, 1.0]


class TestDeviceAndGuard:
    def test_resolve_device_rejects_unknown(self) -> None:
        with pytest.raises(STTError) as excinfo:
            resolve_device("vulkan")
        assert excinfo.value.code == E_STT_FAILED

    def test_resolve_device_passes_explicit_through(self) -> None:
        assert resolve_device("cpu") == "cpu"
        assert resolve_device("cuda") == "cuda"

    def test_resolve_auto_defaults_to_cpu_without_cuda(self) -> None:
        assert resolve_device("auto") in ("cpu", "cuda")

    def test_pick_compute_type(self) -> None:
        assert pick_compute_type("cuda") == "int8_float16"
        assert pick_compute_type("cpu") == "int8"

    def test_guard_model_tier_keeps_when_unknown_vram(self) -> None:
        assert guard_model_tier("large-v3", None) == "large-v3"

    def test_guard_model_tier_downgrades_when_short(self) -> None:
        assert guard_model_tier("large-v3", 3000.0) == "large-v3"
        assert guard_model_tier("large-v3", 2600.0) == "turbo"
        assert guard_model_tier("large-v3", 2000.0) == "small"
        assert guard_model_tier("large-v3", 500.0) == "tiny"
        assert guard_model_tier("turbo", 1000.0) == "base"


class TestTranscribeWithInjectedModel:
    def test_injects_model_and_wires_vad(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        model = FakeModel(
            [FakeSegment(0.0, 0.9, "hello", language="en")],
            FakeInfo(language="en", duration=1.0),
        )
        result = transcribe(
            str(audio),
            project_id="proj-1",
            model_name="large-v3",
            device="cpu",
            whisper_model=model,
        )
        assert result.transcript["segments"][0]["text"] == "hello"
        assert result.model_used == "large-v3"
        assert result.device_used == "cpu"
        assert model.calls and model.calls[0]["vad_filter"] is True
        assert model.calls[0]["beam_size"] == 5

    def test_language_override_wins(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        model = FakeModel([FakeSegment(0.0, 1.0, "hi", language="en")], FakeInfo("en"))
        result = transcribe(
            str(audio),
            project_id="p",
            model_name="turbo",
            device="cpu",
            language="zh",
            whisper_model=model,
        )
        assert result.transcript["language"] == "zh"

    def test_missing_audio_raises(self, tmp_path) -> None:
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(tmp_path / "nope.wav"),
                project_id="p",
                model_name="large-v3",
                whisper_model=FakeModel([], FakeInfo("en")),
            )
        assert excinfo.value.code == E_STT_FAILED

    def test_cancel_before_start_aborts(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            transcribe(
                str(audio),
                project_id="p",
                model_name="large-v3",
                whisper_model=FakeModel([], FakeInfo("en")),
                cancel=token,
            )

    def test_no_speech_propagates(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        model = FakeModel([], FakeInfo("en"))
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="large-v3",
                whisper_model=model,
            )
        assert excinfo.value.code == E_STT_NO_SPEECH

    def test_duration_comes_from_info_when_not_given(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        model = FakeModel(
            [FakeSegment(0.0, 1.5, "hello", language="en")],
            FakeInfo(language="en", duration=3.0),
        )
        ratios: list[float] = []
        result = transcribe(
            str(audio),
            project_id="p",
            model_name="large-v3",
            whisper_model=model,
            on_progress=ratios.append,
        )
        assert ratios == [0.5]
        assert result.transcript["segments"][0]["end"] == 1.5


class CudaBrokenModel:
    """A model whose ``transcribe`` returns a lazy generator that raises on the
    first iteration — exactly how a missing ``cublas64_12.dll`` surfaces in
    faster-whisper (inference happens while the segments generator is
    consumed, not inside ``transcribe()``)."""

    def __init__(self, message: str) -> None:
        self.message = message

    def transcribe(self, audio, **kwargs):
        def gen():
            raise RuntimeError(self.message)
            yield  # pragma: no cover

        return (gen(), None)


class TestCudaLibraryErrorClassifier:
    def test_missing_cublas_is_a_cuda_library_error(self) -> None:
        assert _is_cuda_library_error(
            RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        )

    def test_missing_cudnn_is_a_cuda_library_error(self) -> None:
        assert _is_cuda_library_error(RuntimeError("cudnn64_9.dll cannot be loaded"))

    def test_unrelated_runtime_error_is_not(self) -> None:
        assert not _is_cuda_library_error(RuntimeError("boom"))


class TestCudaToCpuFallback:
    def test_cuda_runtime_error_retries_on_cpu(self, tmp_path, monkeypatch) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        broken = CudaBrokenModel(
            "Library cublas64_12.dll is not found or cannot be loaded"
        )
        cpu_model = FakeModel(
            [FakeSegment(0.0, 0.9, "hello", language="en")],
            FakeInfo(language="en", duration=1.0),
        )
        monkeypatch.setattr(stt_service, "_load_whisper_model", lambda *a, **k: cpu_model)

        result = transcribe(
            str(audio),
            project_id="p",
            model_name="turbo",
            device="cuda",
            whisper_model=broken,
        )

        assert result.device_used == "cpu"
        assert result.model_used == "turbo"
        assert result.transcript["segments"][0]["text"] == "hello"

    def test_non_cuda_runtime_error_still_fails(self, tmp_path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        broken = CudaBrokenModel("boom")
        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="turbo",
                device="cuda",
                whisper_model=broken,
            )
        assert excinfo.value.code == E_STT_FAILED

    def test_cpu_fallback_also_failing_raises_clean_error(self, tmp_path, monkeypatch) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        broken = CudaBrokenModel(
            "Library cublas64_12.dll is not found or cannot be loaded"
        )
        also_broken = CudaBrokenModel("cpu also exploded")
        monkeypatch.setattr(stt_service, "_load_whisper_model", lambda *a, **k: also_broken)

        with pytest.raises(STTError) as excinfo:
            transcribe(
                str(audio),
                project_id="p",
                model_name="turbo",
                device="cuda",
                whisper_model=broken,
            )
        assert excinfo.value.code == E_STT_FAILED
