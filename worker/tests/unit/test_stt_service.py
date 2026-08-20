"""Unit tests for stt_service faster-whisper model loading / thread budget.

Covers the chunked-pipeline CPU fix: ``transcribe(..., cpu_threads=...)`` is
forwarded into ``_load_whisper_model``, and the loader passes it to the
``WhisperModel`` constructor (only when set) while keeping one cached instance
per (model, device, compute_type, cpu_threads). No real model is loaded here —
faster-whisper is faked through ``sys.modules`` (see test_tts_voice_library.py).
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import src.services.stt_service as stt_service


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """The module-level model cache is a global shared across tests; always
    start and end clean so a cached instance never leaks between tests."""
    stt_service._WHISPER_MODEL_CACHE.clear()
    yield
    stt_service._WHISPER_MODEL_CACHE.clear()


def test_transcribe_forwards_cpu_threads_to_loader(tmp_path, monkeypatch):
    """``transcribe(..., cpu_threads=...)`` must reach ``_load_whisper_model``
    so the chunked pipeline's per-call thread budget actually applies (the
    resolved device here is CPU, matching the chunked pipeline's usage)."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00" * 100)

    captured: dict = {}
    fake_model = SimpleNamespace(
        transcribe=lambda audio_path, **kw: (  # noqa: ARG005
            [SimpleNamespace(text="hi", start=0.0, end=0.5, language="en", avg_logprob=-0.1)],
            SimpleNamespace(language="en", duration=0.5),
        )
    )

    def fake_loader(model_name, device, compute_type, cpu_threads=None):
        captured.update(
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
        )
        return fake_model

    monkeypatch.setattr(stt_service, "_load_whisper_model", fake_loader)

    result = stt_service.transcribe(
        str(audio),
        project_id="p",
        model_name="large-v3",
        device="cpu",
        cpu_threads=2,
    )

    assert captured["cpu_threads"] == 2
    assert captured["device"] == "cpu"
    assert captured["model_name"] == "large-v3"
    assert result.transcript["segments"][0]["text"] == "hi"


class _FakeWhisperModel:
    """Records every constructor call — faster-whisper is never imported."""

    calls: list[dict] = []

    def __init__(self, model_name: str, **kwargs) -> None:
        _FakeWhisperModel.calls.append({"model_name": model_name, **kwargs})


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _FakeWhisperModel
    _FakeWhisperModel.calls = []
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(stt_service, "ensure_cuda_libraries", lambda: None)
    yield
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)


def test_load_whisper_model_forwards_cpu_threads_and_cache_key(fake_faster_whisper):
    """``_load_whisper_model(..., cpu_threads=...)`` passes the kwarg to the
    ``WhisperModel`` constructor (local_files_only branch) and keys the shared
    cache on cpu_threads — a different thread budget is a different instance."""
    model_a1 = stt_service._load_whisper_model("small", "cpu", "int8", cpu_threads=2)
    model_a2 = stt_service._load_whisper_model("small", "cpu", "int8", cpu_threads=2)
    model_b = stt_service._load_whisper_model("small", "cpu", "int8", cpu_threads=None)

    # cpu_threads=2 reached the constructor on the local_files_only branch.
    assert _FakeWhisperModel.calls[0]["cpu_threads"] == 2
    assert _FakeWhisperModel.calls[0]["local_files_only"] is True
    assert _FakeWhisperModel.calls[0]["model_name"] == "small"
    # Same (model, device, compute_type, cpu_threads) key → cached instance.
    assert model_a1 is model_a2
    # cpu_threads=None is a DIFFERENT cache key → separate load (only 2 loads
    # total: 2× same key + 1× None key), and the constructor must NOT receive
    # the kwarg so the faster-whisper default (all cores) is preserved.
    assert len(_FakeWhisperModel.calls) == 2
    assert "cpu_threads" not in _FakeWhisperModel.calls[1]
    assert model_a1 is not model_b


def test_load_whisper_model_cached_hit_skips_import(fake_faster_whisper):
    """A cached (model, device, compute_type, cpu_threads) instance is returned
    without constructing faster-whisper again; a different cpu_threads key is a
    separate entry even for the same model/device/compute."""
    sentinel = object()
    stt_service._WHISPER_MODEL_CACHE[("medium", "cpu", "int8", None)] = sentinel

    got = stt_service._load_whisper_model("medium", "cpu", "int8", cpu_threads=None)
    assert got is sentinel

    got2 = stt_service._load_whisper_model("medium", "cpu", "int8", cpu_threads=4)
    assert got2 is not sentinel
    # Cache hit records no construction; the new key records exactly one.
    assert len(_FakeWhisperModel.calls) == 1