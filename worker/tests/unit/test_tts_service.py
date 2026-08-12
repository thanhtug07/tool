"""Unit tests for TTSService — mocked engines, no network, no model download.

The engine runners (edge-tts / piper) are monkeypatched with a fake that
writes a known 44.1 kHz mono WAV, so the pipeline logic (voice resolution,
per-cue synthesis, atempo fitting, full-duration track assembly, cancel,
progress) is exercised without any external call.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

import src.services.tts_service as tts_service
from src.core.job import CancelledError, CancellationToken
from src.services.tts_service import (
    E_TTS_FAILED,
    E_TTS_UNAVAILABLE,
    TTSCue,
    TTSError,
    _fit_atempo,
    synthesize_cues,
    validate_voice,
)

SR = tts_service.TRACK_SAMPLE_RATE


def _write_sine_wav(path: str, seconds: float, freq: float = 440.0) -> None:
    """Write a ``seconds``-long mono 16-bit 44.1 kHz sine WAV."""
    frames = int(seconds * SR)
    data = bytearray()
    for i in range(frames):
        sample = int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / SR))
        data += struct.pack("<h", sample)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(data))


def _fake_edge(text: str, voice: str, out_wav: str) -> float:
    # 1.0 s of audio per call — the standard fake used by the tests.
    _write_sine_wav(out_wav, 1.0)
    return 1.0


class TestValidateVoice:
    def test_defaults_vi_to_hoaimy(self) -> None:
        engine, voice = validate_voice("edge", None, "vi")
        assert (engine, voice) == ("edge", "vi-VN-HoaiMyNeural")

    def test_explicit_voice_passes_through(self) -> None:
        assert validate_voice("edge", "vi-VN-NamMinhNeural", "vi") == ("edge", "vi-VN-NamMinhNeural")

    def test_unknown_engine_raises(self) -> None:
        with pytest.raises(TTSError) as excinfo:
            validate_voice("kokoro", None, "vi")
        assert excinfo.value.code == E_TTS_UNAVAILABLE

    def test_unknown_voice_raises(self) -> None:
        with pytest.raises(TTSError) as excinfo:
            validate_voice("edge", "vi-NOPE-Neural", "vi")
        assert excinfo.value.code == E_TTS_UNAVAILABLE


class TestFitAtempo:
    def test_fits_inside_window_unchanged(self) -> None:
        assert _fit_atempo(2.0, 4.0) is None

    def test_speeds_up_overflow(self) -> None:
        assert _fit_atempo(2.5, 2.0) == pytest.approx(1.25)

    def test_caps_at_max(self) -> None:
        assert _fit_atempo(10.0, 1.0) == pytest.approx(tts_service.MAX_FIT_ATEMPO)

    def test_tiny_window_never_fits(self) -> None:
        assert _fit_atempo(5.0, 0.2) is None


class TestSynthesizeCues:
    def test_assembles_full_duration_track(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tts_service, "_synthesize_edge", _fake_edge)
        cues = [
            TTSCue(start=0.0, end=2.0, text="xin chào"),
            TTSCue(start=2.0, end=4.0, text="tôi là giọng đọc"),
            TTSCue(start=10.0, end=12.0, text="câu ở giữa"),
        ]
        result = synthesize_cues(
            cues,
            voice=None,
            engine="edge",
            language="vi",
            duration_seconds=12.0,
            output_dir=str(tmp_path),
        )
        assert result.engine_used == "edge"
        assert result.voice_used == "vi-VN-HoaiMyNeural"
        track = Path(result.voice_track_path)
        assert track.is_file()
        with wave.open(str(track), "rb") as w:
            assert w.getframerate() == SR
            assert w.getnchannels() == 1
            assert w.getnframes() == 12 * SR  # full duration, silence + speech

        # The speech must be placed at the cue start (non-silent around 0.0s
        # and around 10.0s; silent at 6.0s where no cue exists).
        assert _rms(track, 0.0, 0.5) > 0.01
        assert _rms(track, 10.0, 10.5) > 0.01
        assert _rms(track, 6.0, 6.5) < 0.001

        meta = Path(result.meta_path)
        assert meta.is_file()
        payload = meta.read_text(encoding="utf-8")
        assert "voice" in payload and "xin chào" in payload

    def test_long_speech_is_time_fitted_into_short_cue(self, tmp_path, monkeypatch) -> None:
        """Regression: the atempo fit branch must leave the cue wav in place."""
        monkeypatch.setattr(tts_service, "_synthesize_edge", _fake_edge)  # 1.0 s speech
        cues = [TTSCue(start=0.0, end=0.5, text="câu ngắn nhưng đọc dài")]  # 0.5 s window
        result = synthesize_cues(
            cues,
            voice="vi-VN-HoaiMyNeural",
            engine="edge",
            language="vi",
            duration_seconds=1.0,
            output_dir=str(tmp_path),
        )
        # The original cue file must exist (not renamed to *.fit.wav) and the
        # track must assemble from it.
        cue = tmp_path / "cue_00000.wav"
        assert cue.is_file(), "fitted cue wav must stay at its canonical path"
        assert not (tmp_path / "cue_00000.wav.fit.wav").exists()
        track = Path(result.voice_track_path)
        assert track.is_file()
        with wave.open(str(track), "rb") as w:
            assert w.getnframes() == 1 * SR  # full duration
        assert result.meta_path and Path(result.meta_path).is_file()

    def test_progress_reports_each_cue(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tts_service, "_synthesize_edge", _fake_edge)
        cues = [TTSCue(start=i, end=i + 1, text=f"t{i}") for i in range(4)]
        ratios: list[float] = []
        synthesize_cues(
            cues,
            voice="vi-VN-NamMinhNeural",
            engine="edge",
            language="vi",
            duration_seconds=4.0,
            output_dir=str(tmp_path),
            on_progress=ratios.append,
        )
        assert ratios == [0.25, 0.5, 0.75, 1.0]

    def test_cancel_before_start_aborts(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(tts_service, "_synthesize_edge", _fake_edge)
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            synthesize_cues(
                [TTSCue(start=0, end=1, text="x")],
                voice=None,
                engine="edge",
                language="vi",
                duration_seconds=1.0,
                output_dir=str(tmp_path),
                cancel=token,
            )

    def test_synthesis_failure_raises_clean_error(self, tmp_path, monkeypatch) -> None:
        def broken(text: str, voice: str, out_wav: str) -> float:
            raise TTSError(E_TTS_FAILED, "edge-tts synthesis failed: boom")

        monkeypatch.setattr(tts_service, "_synthesize_edge", broken)
        with pytest.raises(TTSError) as excinfo:
            synthesize_cues(
                [TTSCue(start=0, end=1, text="x")],
                voice=None,
                engine="edge",
                language="vi",
                duration_seconds=1.0,
                output_dir=str(tmp_path),
            )
        assert excinfo.value.code == E_TTS_FAILED

    def test_unavailable_engine_raises(self, tmp_path) -> None:
        with pytest.raises(TTSError) as excinfo:
            synthesize_cues(
                [TTSCue(start=0, end=1, text="x")],
                voice=None,
                engine="kokoro",
                language="vi",
                duration_seconds=1.0,
                output_dir=str(tmp_path),
            )
        assert excinfo.value.code == E_TTS_UNAVAILABLE


def _rms(track: Path, start_s: float, end_s: float) -> float:
    """RMS amplitude of the track within [start_s, end_s)."""
    with wave.open(str(track), "rb") as w:
        w.setpos(int(start_s * SR))
        frames = w.readframes(int((end_s - start_s) * SR))
    count = len(frames) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", frames[: count * 2])
    return (sum(s * s for s in samples) / count) ** 0.5 / 32767.0
