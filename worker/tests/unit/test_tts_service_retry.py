"""Regression tests for the edge-tts retry-with-backoff fix.

edge-tts (free cloud service) intermittently answers a request with metadata
but no audio ("No audio was received"). Before the fix that aborted the whole
dubbing stage; now `_synthesize_edge` retries the failing cue a few times.

These tests fake the `edge_tts` module (its `save()` writes a valid 44.1 kHz
mono WAV directly, so the ffmpeg normalization step in `_normalize_to_wav`
still runs and the real wave reader works) and count how often `save` is
called.
"""
from __future__ import annotations

import sys
import types
import wave
from collections import OrderedDict

import pytest

from src.services import tts_service


class _FlakyEdge:
    """Fake `edge_tts` whose Communicate.save fails `failures` times first."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    class Communicate:
        def __init__(self, text: str, voice: str, parent: "_FlakyEdge"):
            self._text = text
            self._voice = voice
            self._parent = parent

        async def save(self, path: str) -> None:
            self._parent.calls += 1
            if self._parent.calls <= self._parent.failures:
                raise RuntimeError(
                    "No audio was received. Please verify that your parameters are correct."
                )
            # Write a valid 44.1k mono 16-bit WAV (0.2 s of silence) so the
            # real `_normalize_to_wav` ffmpeg step + wave reader succeed.
            frames = bytearray(b"\x00\x00" * int(44100 * 0.2))
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(bytes(frames))


@pytest.fixture
def fake_edge(monkeypatch):
    """Install a fake `edge_tts` module and return the factory to configure it."""
    installed = {}

    def install(failures: int):
        fake = _FlakyEdge(failures)
        mod = types.ModuleType("edge_tts")
        mod.Communicate = lambda text, voice: fake.Communicate(text, voice, fake)
        installed["edge_tts"] = mod
        monkeypatch.setitem(sys.modules, "edge_tts", mod)
        return fake

    yield install
    monkeypatch.delitem(sys.modules, "edge_tts", raising=False)
    _ = installed


def _call_edge(text: str, out_wav: str) -> float:
    # `_synthesize_edge` imports edge_tts lazily inside the function, so the
    # sys.modules patch from the fixture is picked up at call time.
    return tts_service._synthesize_edge(text, "vi-VN-HoaiMyNeural", out_wav)


def test_retries_then_succeeds_after_transient_failures(fake_edge, tmp_path):
    fake = fake_edge(2)  # fail twice, succeed on the 3rd attempt
    out = str(tmp_path / "cue.wav")
    duration = _call_edge("Xin chào thế giới", out)
    assert fake.calls == 3
    assert duration > 0
    assert wave.open(out, "rb").getframerate() == 44100


def test_succeeds_without_retry_when_no_failure(fake_edge, tmp_path):
    fake = fake_edge(0)
    out = str(tmp_path / "cue.wav")
    _call_edge("Hello", out)
    assert fake.calls == 1


def test_raises_tts_error_after_max_attempts(fake_edge, tmp_path):
    fake = fake_edge(99)  # always fails
    with pytest.raises(tts_service.TTSError) as excinfo:
        _call_edge("Hello", str(tmp_path / "cue.wav"))
    assert excinfo.value.code == "E_TTS_FAILED"
    assert "edge-tts synthesis failed" in excinfo.value.message
    assert fake.calls == tts_service._EDGE_MAX_ATTEMPTS


class _FakePiperMessage:
    def __init__(self, index_data, output_bytes=0):
        self._chunk = bytes(index_data)
        self._output_bytes = output_bytes


class _FakePiperVoice:
    """Fake ``piper.PiperVoice`` counting ``load`` calls (single process-wide
    cache must hit, not reload, on repeat voices)."""

    loads = 0

    def __init__(self, model_path: str):
        self.model_path = model_path

    @classmethod
    def load(cls, model_path: str) -> "_FakePiperVoice":
        cls.loads += 1
        return cls(model_path)

    def synthesize(
        self,
        text: str,
        sink,  # noqa: ANN001 - file-like (legacy rhasspy API)
        synthesize_kwargs=None,  # noqa: ANN001
    ) -> None:
        # A valid 44.1k mono 16-bit WAV (0.2 s of silence) so the real
        # `_normalize_to_wav` + wave reader succeed.
        frames = bytearray(b"\x00\x00" * int(44100 * 0.2))
        with wave.open(sink, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(bytes(frames))


@pytest.fixture
def fake_piper(monkeypatch):
    """Install a fake ``piper`` module and stub model resolution/voice cache."""
    import sys

    pip_voice = _FakePiperVoice
    pip_voice.loads = 0
    mod = types.ModuleType("piper")
    mod.PiperVoice = pip_voice
    monkeypatch.setitem(sys.modules, "piper", mod)

    cached = {}

    cache = OrderedDict()
    monkeypatch.setattr(tts_service, "_PIPER_VOICE_CACHE", cache)
    monkeypatch.setattr(tts_service, "_PIPER_VOICE_CACHE_MAX", 2)
    monkeypatch.setattr(
        tts_service,
        "_piper_model_path",
        lambda voice: (f"/models/{voice}.onnx", f"/models/{voice}.json"),
    )

    yield pip_voice
    monkeypatch.delitem(sys.modules, "piper", raising=False)


def _call_piper(text: str, voice: str, out_wav: str) -> float:
    return tts_service._synthesize_piper(text, voice, out_wav)


def test_piper_voice_loaded_once_across_cues(fake_piper, tmp_path):
    fake_piper.loads = 0
    _call_piper("Xin chào", "vi-VN-vais1000-medium", str(tmp_path / "a.wav"))
    _call_piper("Xin chào thế giới", "vi-VN-vais1000-medium", str(tmp_path / "b.wav"))
    _call_piper("Hello", "vi-VN-vais1000-medium", str(tmp_path / "c.wav"))
    assert fake_piper.loads == 1  # one ONNX parse for the whole run, not per cue


def test_piper_voice_cache_evicts_to_max(fake_piper, tmp_path):
    fake_piper.loads = 0
    _call_piper("a", "voice-a", str(tmp_path / "a.wav"))
    _call_piper("b", "voice-b", str(tmp_path / "b.wav"))
    _call_piper("c", "voice-c", str(tmp_path / "c.wav"))  # evicts voice-a (LRU cap 2)
    _call_piper("a", "voice-a", str(tmp_path / "d.wav"))
    assert fake_piper.loads == 4  # voice-a reloaded after eviction
