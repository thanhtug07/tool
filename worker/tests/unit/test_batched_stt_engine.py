"""Unit tests for the batched STT engine (BATCHED_STT task).

Covers the STTEngine contract (RegularSTTEngine == BatchedSTTEngine normalized
output shape), ``stt_mode`` auto/regular/batched selection, batch-size
validation, timestamp validation and the batched→regular fallback. No real
model or GPU is touched — faster-whisper's ``BatchedInferencePipeline`` is
faked through ``sys.modules`` and the model is a lightweight double.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import src.services.stt_service as stt_service


@pytest.fixture(autouse=True)
def _clear_model_cache():
    stt_service._WHISPER_MODEL_CACHE.clear()
    yield
    stt_service._WHISPER_MODEL_CACHE.clear()


def _word_dict(start: float, end: float, text: str, prob: float = 0.9):
    return {"start": start, "end": end, "text": text, "probability": prob}


def _word(start: float, end: float, text: str, prob: float = 0.9):
    return SimpleNamespace(start=start, end=end, word=text, probability=prob)


def _seg(words, start=None, end=None):
    start = float(start if start is not None else (words[0].start if words else 0.0))
    end = float(end if end is not None else (words[-1].end if words else start))
    return SimpleNamespace(
        start=start,
        end=end,
        text=" ".join(w.word for w in words),
        language="en",
        words=words,
    )


def _fake_model(segments):
    """A faster-whisper double whose ``transcribe`` returns ``segments``."""
    return SimpleNamespace(
        transcribe=lambda audio, **kw: (  # noqa: ARG005
            list(segments),
            SimpleNamespace(language="en", duration=10.0),
        ),
    )


# -- mode selection -----------------------------------------------------------


def test_auto_selects_batched_when_cuda_and_vram_safe():
    mode, reason = stt_service.resolve_stt_mode(
        "auto", device="cuda", model_name="small", batch_size=2, vram_mb=6000.0
    )
    assert mode == stt_service.STT_MODE_BATCHED
    assert "auto" in reason


def test_auto_regular_on_cpu():
    mode, reason = stt_service.resolve_stt_mode(
        "auto", device="cpu", model_name="small", batch_size=2, vram_mb=None
    )
    assert mode == stt_service.STT_MODE_REGULAR
    assert "cpu" in reason


def test_auto_regular_when_vram_unknown():
    mode, _ = stt_service.resolve_stt_mode(
        "auto", device="cuda", model_name="small", batch_size=2, vram_mb=None
    )
    assert mode == stt_service.STT_MODE_REGULAR


def test_auto_regular_when_vram_low():
    # small needs 1200 MB + 400 MB margin -> 1600 MB; 1000 MB is unsafe.
    mode, reason = stt_service.resolve_stt_mode(
        "auto", device="cuda", model_name="small", batch_size=2, vram_mb=1000.0
    )
    assert mode == stt_service.STT_MODE_REGULAR
    assert "VRAM" in reason


def test_explicit_modes_are_honoured():
    assert stt_service.resolve_stt_mode("regular", device="cuda", model_name="x", batch_size=2, vram_mb=1.0)[0] == "regular"
    assert stt_service.resolve_stt_mode("batched", device="cpu", model_name="x", batch_size=2, vram_mb=1.0)[0] == "batched"


def test_invalid_mode_and_batch_size_rejected():
    with pytest.raises(stt_service.STTError):
        stt_service.resolve_stt_mode("nope", device="cuda", model_name="small", batch_size=2, vram_mb=6000.0)
    with pytest.raises(stt_service.STTError):
        stt_service.resolve_stt_mode("auto", device="cuda", model_name="small", batch_size=8, vram_mb=6000.0)


# -- reconstruction -----------------------------------------------------------


def test_reconstruct_splits_on_sentence_and_gap():
    words = [
        _word_dict(0.0, 0.5, "Hello"),
        _word_dict(0.5, 1.0, "world."),
        # 2s silence: a real speech pause
        _word_dict(3.0, 3.4, "Next"),
        _word_dict(3.4, 3.9, "sentence."),
    ]
    segs = stt_service.reconstruct_segments_from_words(words, language="en")
    assert [s["text"] for s in segs] == ["Hello world.", "Next sentence."]
    assert all(s["start"] >= 0.0 and s["end"] >= s["start"] for s in segs)
    assert all("words" in s for s in segs)


def test_reconstruct_respects_max_duration_cap():
    # Many words across >8s with no punctuation -> capped by duration.
    words = [_word_dict(i * 0.5, i * 0.5 + 0.4, f"w{i}", prob=0.8) for i in range(30)]
    segs = stt_service.reconstruct_segments_from_words(words, language="en")
    assert len(segs) >= 2
    for s in segs:
        assert (s["end"] - s["start"]) <= stt_service.RECON_MAX_SEGMENT_SECONDS + 1e-6


def test_reconstruct_output_shape_matches_build_transcript():
    words = [_word_dict(0.0, 0.5, "Hi"), _word_dict(0.5, 1.2, "there.")]
    segs = stt_service.reconstruct_segments_from_words(words, language="en")
    wrapped = [SimpleNamespace(**s) for s in segs]
    transcript = stt_service.build_transcript(
        wrapped,
        project_id="p",
        model_name="small",
        language_override="en",
    )
    assert transcript["segments"][0]["text"] == "Hi there."
    assert transcript["segments"][0]["confidence"] > 0.8
    assert "words" in transcript["segments"][0]


# -- timestamp validation -----------------------------------------------------


def test_validate_timestamps_accepts_clean_sequence():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 1.0, "end": 2.0, "text": "b"},
    ]
    assert stt_service.validate_segment_timestamps(segs) == []


def test_validate_timestamps_rejects_corruption():
    issues = stt_service.validate_segment_timestamps(
        [
            {"start": -1.0, "end": 1.0, "text": "neg"},
            {"start": 0.0, "end": -0.5, "text": "bad"},
            {"start": 5.0, "end": 4.0, "text": "flipped"},
        ]
    )
    assert any("negative" in i for i in issues)
    assert any("end" in i and "start" in i for i in issues)


def test_validate_timestamps_rejects_duplicate_and_overlap():
    issues = stt_service.validate_segment_timestamps(
        [
            {"start": 0.0, "end": 1.0, "text": "same"},
            {"start": 0.0, "end": 1.0, "text": "same"},
            {"start": 0.5, "end": 1.5, "text": "overlap"},
        ]
    )
    assert any("duplicate" in i for i in issues)
    assert any("overlap" in i for i in issues)


def test_validate_timestamps_tolerates_reconstruct_boundary_rounding():
    # 0.0009s overlap is rounding at a boundary, not corruption.
    segs = [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 0.9991, "end": 2.0, "text": "b"},
    ]
    assert stt_service.validate_segment_timestamps(segs) == []


# -- transcribe() engine dispatch + fallback ----------------------------------


def test_transcribe_auto_on_cpu_uses_regular_engine(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 100)
    model = _fake_model([SimpleNamespace(text="hi", start=0.0, end=0.5, language="en", avg_logprob=-0.1)])

    result = stt_service.transcribe(
        str(audio),
        project_id="p",
        model_name="small",
        device="cpu",
        whisper_model=model,
    )
    assert result.engine == stt_service.STT_MODE_REGULAR
    assert result.transcript["segments"][0]["text"] == "hi"


def test_transcribe_batched_uses_batched_engine(tmp_path, monkeypatch):
    """Fake faster-whisper ``BatchedInferencePipeline`` such that
    ``transcribe(stt_mode="batched")`` routes through the batched path and
    returns the reconstructed normalized segments."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 100)

    class _FakeBatchedPipeline:
        def __init__(self, model):
            self.model = model

        def transcribe(self, audio, **kw):  # noqa: ARG002
            words = [_word(0.0, 0.5, "Hello"), _word(0.5, 1.0, "world.")]
            return [  # raw batched window segments
                SimpleNamespace(
                    start=0.0,
                    end=1.0,
                    text="Hello world.",
                    language="en",
                    words=words,
                )
            ], SimpleNamespace(language="en", duration=10.0)

    fw_mod = types.ModuleType("faster_whisper")
    fw_transcribe = types.ModuleType("faster_whisper.transcribe")
    fw_transcribe.BatchedInferencePipeline = _FakeBatchedPipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_mod)
    monkeypatch.setitem(sys.modules, "faster_whisper.transcribe", fw_transcribe)

    model = _fake_model([])  # regular path never invoked
    result = stt_service.transcribe(
        str(audio),
        project_id="p",
        model_name="small",
        device="cuda",
        whisper_model=model,
        stt_mode="batched",
        batch_size=2,
    )
    assert result.engine == stt_service.STT_MODE_BATCHED
    assert result.transcript["segments"][0]["text"] == "Hello world."
    assert "words" in result.transcript["segments"][0]


def test_fallback_to_regular_on_batched_runtime_failure(tmp_path, monkeypatch):
    """If the batched pipeline raises, transcribe() must NOT fail the job: it
    logs [BATCHED_STT] Failed / Reason + falls back to regular mode."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 100)

    class _ExplodingPipeline:
        def __init__(self, model):
            pass

        def transcribe(self, audio, **kw):  # noqa: ARG002
            raise RuntimeError("cuda out of memory")

    fw_mod = types.ModuleType("faster_whisper")
    fw_transcribe = types.ModuleType("faster_whisper.transcribe")
    fw_transcribe.BatchedInferencePipeline = _ExplodingPipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_mod)
    monkeypatch.setitem(sys.modules, "faster_whisper.transcribe", fw_transcribe)

    model = _fake_model([SimpleNamespace(text="fallback ok", start=0.0, end=0.5, language="en", avg_logprob=-0.1)])
    logs: list[str] = []
    result = stt_service.transcribe(
        str(audio),
        project_id="p",
        model_name="small",
        device="cuda",
        whisper_model=model,
        stt_mode="batched",
        batch_size=2,
        on_stt_log=logs.append,
    )
    assert result.engine == stt_service.STT_MODE_REGULAR
    assert result.transcript["segments"][0]["text"] == "fallback ok"
    joined = "\n".join(logs)
    assert "[BATCHED_STT] Failed" in joined
    assert "[BATCHED_STT] Reason:" in joined
    assert "[STT] Falling back to regular mode" in joined


def test_fallback_on_timestamp_validation_rejection(tmp_path, monkeypatch):
    """Invalid batched output (non-monotonic) is rejected by validation and
    falls back to regular — no corrupt timestamps ever reach downstream."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 100)

    class _GarbagePipeline:
        def __init__(self, model):
            pass

        def transcribe(self, audio, **kw):  # noqa: ARG002
            # Two sentences from overlapping windows: the second begins before
            # the first ends -> reconstructed segments overlap, which the
            # timestamp validation must reject (corrupt output never reaches
            # downstream).
            words = [
                _word(0.0, 0.5, "Sentence"),
                _word(0.5, 1.0, "one."),
                _word(0.8, 1.3, "Overlap"),
                _word(1.3, 2.0, "two."),
            ]
            return [
                SimpleNamespace(start=0.0, end=1.0, text="Sentence one.", language="en", words=words[:2]),
                SimpleNamespace(start=0.8, end=2.0, text="Overlap two.", language="en", words=words[2:]),
            ], SimpleNamespace(language="en", duration=10.0)

    fw_mod = types.ModuleType("faster_whisper")
    fw_transcribe = types.ModuleType("faster_whisper.transcribe")
    fw_transcribe.BatchedInferencePipeline = _GarbagePipeline
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_mod)
    monkeypatch.setitem(sys.modules, "faster_whisper.transcribe", fw_transcribe)

    model = _fake_model([SimpleNamespace(text="safe", start=0.0, end=0.5, language="en", avg_logprob=-0.1)])
    logs: list[str] = []
    result = stt_service.transcribe(
        str(audio),
        project_id="p",
        model_name="small",
        device="cuda",
        whisper_model=model,
        stt_mode="batched",
        batch_size=2,
        on_stt_log=logs.append,
    )
    assert result.engine == stt_service.STT_MODE_REGULAR
    assert result.transcript["segments"][0]["text"] == "safe"
    joined = "\n".join(logs)
    assert "overlap" in joined  # reason surfaced in [BATCHED_STT] Reason