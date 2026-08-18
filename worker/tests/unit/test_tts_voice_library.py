"""Voice Library tests.

Covers the voice registry that powers the Voice Library:
- every registered voice (edge + piper) carries real metadata (language,
  gender, age, preview text) — nothing invented;
- `voice_meta` reports "Not specified" when the provider does not expose a
  field (piper gender, etc.);
- `POST /v1/tts/preview` synthesizes a real clip through the engine and caches
  the file by engine+voice+text hash.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import wave
from pathlib import Path

import pytest

from src.services import tts_service


@pytest.fixture
def piper_installed(monkeypatch):
    """Pretend the piper package exists so validate_voice reaches its table."""
    mod = types.ModuleType("piper")
    monkeypatch.setitem(sys.modules, "piper", mod)
    yield
    monkeypatch.delitem(sys.modules, "piper", raising=False)


def test_default_voice_matches_language_engine(piper_installed):
    """Default voices are chosen from the engine's own registry with a native
    voice for that language. An engine with no model for a language fails
    loudly (``E_TTS_UNAVAILABLE``) instead of silently dubbing in a
    Vietnamese voice (FIX #4, review 2026-08-18)."""
    assert tts_service.validate_voice("edge", None, "en")[1] == "en-US-AriaNeural"
    assert tts_service.validate_voice("edge", None, "ja")[1] == "ja-JP-NanamiNeural"
    assert tts_service.validate_voice("edge", None, "ko")[1] == "ko-KR-SunHiNeural"
    assert tts_service.validate_voice("edge", None, "vi")[1] == "vi-VN-HoaiMyNeural"
    assert tts_service.validate_voice("edge", None, "zh")[1] == "zh-CN-XiaoxiaoNeural"
    assert tts_service.validate_voice("edge", None, "fr")[1] == "fr-FR-DeniseNeural"
    assert tts_service.validate_voice("edge", None, "de")[1] == "de-DE-KatjaNeural"
    assert tts_service.validate_voice("edge", None, "es")[1] == "es-ES-ElviraNeural"
    assert tts_service.validate_voice("piper", None, "vi")[1] == "vi_VN-vais1000-medium"
    assert tts_service.validate_voice("piper", None, "zh")[1] == "zh_CN-huayan-medium"


def test_piper_unsupported_language_raises_instead_of_vietnamese(piper_installed):
    """piper ships vi + zh models only — en/ja/ko must raise a clear
    ``E_TTS_UNAVAILABLE`` rather than silently dubbing with the Vietnamese
    voice (the pre-fix behaviour)."""
    for lang in ("en", "ja", "ko", "fr"):
        with pytest.raises(tts_service.TTSError) as exc:
            tts_service.validate_voice("piper", None, lang)
        assert exc.value.code == "E_TTS_UNAVAILABLE"


def test_unknown_language_has_no_silent_vietnamese_fallback(piper_installed):
    """A language with no default no longer falls back to a Vietnamese voice
    for edge either — it fails loudly so the user can pick a voice."""
    with pytest.raises(tts_service.TTSError) as exc:
        tts_service.validate_voice("edge", None, "xx")
    assert exc.value.code == "E_TTS_UNAVAILABLE"


def test_every_voice_has_honest_metadata():
    """Every voice in the registry is described; gender/age are valid or
    explicitly \"Not specified\"; language has a real preview sentence."""
    for voice in {**tts_service.EDGE_VOICES, **tts_service.PIPER_VOICES}:
        meta = tts_service.voice_meta("edge", voice)
        assert meta["language"] in tts_service.PREVIEW_TEXTS, voice
        assert meta["gender"] in ("female", "male", "Not specified"), voice
        assert meta["age"] in ("child", "adult", "Not specified"), voice
        assert meta["preview_text"].strip(), voice
        assert isinstance(meta["tags"], list), voice


def test_voice_meta_defaults_to_not_specified():
    """Unknown voices and providers without metadata fall back honestly."""
    assert tts_service.voice_meta("edge", "vi_VN-vais1000-medium")["gender"] == "Not specified"
    unknown = tts_service.voice_meta("edge", "nope-Nope")
    assert unknown["gender"] == "Not specified"
    assert unknown["age"] == "Not specified"
    assert unknown["tags"] == []


def test_voice_library_has_more_voices_than_before():
    """The library must expose a real, broader catalogue (DOD: \"Có nhiều
    voice hơn hiện tại dựa trên provider thực tế\")."""
    assert len(tts_service.EDGE_VOICES) >= 30
    # Multi-language support beyond the original 4 languages.
    langs = {tts_service.voice_meta("edge", v)["language"] for v in tts_service.EDGE_VOICES}
    assert {"vi", "en", "zh", "ja", "ko", "fr", "de", "es"} <= langs


# ---------------------------------------------------------------------------
# Preview endpoint (fake edge-tts writes a valid WAV, like the retry tests)
# ---------------------------------------------------------------------------


class _FakeEdge:
    class Communicate:
        def __init__(self, text: str, voice: str):
            self._text = text
            self._voice = voice

        async def save(self, path: str) -> None:
            frames = bytearray(b"\x00\x00" * int(44100 * 0.2))
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(bytes(frames))


@pytest.fixture
def fake_edge(monkeypatch):
    mod = types.ModuleType("edge_tts")
    mod.Communicate = _FakeEdge.Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", mod)
    yield
    monkeypatch.delitem(sys.modules, "edge_tts", raising=False)


@pytest.fixture
def preview_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def _call_preview(request: dict):
    from src.api import pipeline as api

    req = api.TTSPreviewRequest.model_validate(request)
    resp = api.tts_preview(req)
    return json.loads(resp.body)


def test_preview_synthesizes_real_wav_and_caches(fake_edge, preview_cache_dir):
    req = {"engine": "edge", "voice": "vi-VN-HoaiMyNeural", "text": "Xin chào"}
    first = _call_preview(req)
    assert first["cached"] is False
    assert first["duration_seconds"] > 0
    assert Path(first["path"]).is_file()

    # Same engine+voice+text → served from cache, no re-synthesis.
    second = _call_preview(req)
    assert second["cached"] is True
    assert second["path"] == first["path"]


def test_preview_unknown_voice_is_rejected(fake_edge, preview_cache_dir):
    # The API converts the TTSError into an honest error payload (never a fake
    # preview or a crash).
    result = _call_preview({"engine": "edge", "voice": "nope-Nope", "text": "Hello"})
    assert result.get("error", {}).get("code") == "E_TTS_UNAVAILABLE"
