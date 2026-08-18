"""TTSService — voice synthesis for dubbing (MASTER_PLAN §13, now in build).

Two engines behind one contract:

- **edge-tts** (default): Microsoft Edge neural voices — free, cloud, best
  Vietnamese quality, no model download. Requires network at synthesis time.
- **piper**: fully local fallback (vi_VN-vais1000-medium ONNX model downloaded
  on first use into the HF cache / ``TTS_MODEL_DIR``). Robotic but offline.

``synthesize_cues`` turns translated subtitle cues into a single full-duration
voice track (16-bit mono 44.1 kHz WAV) with each cue's speech placed at its
start time and speed-fitted (``atempo``, max 1.5x) when it would overflow the
cue window. The render stage mixes this track over the original audio
(original ducked to ~45%) — see ``render_service``.

Both engines are lazy-imported. Without either installed, a clean
``E_TTS_UNAVAILABLE`` error is raised — never a crash.

Error codes follow ``E_<MODULE>_*`` (MASTER_PLAN §28.1):
- ``E_TTS_UNAVAILABLE`` — no engine installed / voice unknown.
- ``E_TTS_FAILED`` — synthesis, conversion or assembly failed.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.ffmpeg import resolve_ffmpeg
from src.core.job import CancelledError, CancellationToken

logger = logging.getLogger(__name__)

E_TTS_UNAVAILABLE = "E_TTS_UNAVAILABLE"
E_TTS_FAILED = "E_TTS_FAILED"

ENGINE_EDGE = "edge"
ENGINE_PIPER = "piper"
_VALID_ENGINES = (ENGINE_EDGE, ENGINE_PIPER)

#: Sample rate all engines are normalized to before assembly.
TRACK_SAMPLE_RATE = 44100
#: Mono 16-bit.
TRACK_CHANNELS = 1
TRACK_SAMPLE_WIDTH = 2

#: Speech that overflows its cue window is sped up; never beyond this factor.
MAX_FIT_ATEMPO = 1.5

#: Voice registry: ``{voice_id: label}``. These are REAL Microsoft neural
#: voices served by edge-tts (the ``edge`` engine) — every id is a voice the
#: provider actually synthesizes. Nothing here is invented.
EDGE_VOICES: dict[str, str] = {
    # Vietnamese
    "vi-VN-HoaiMyNeural": "Vietnamese — female (edge)",
    "vi-VN-NamMinhNeural": "Vietnamese — male (edge)",
    # Chinese (Mandarin)
    "zh-CN-XiaoxiaoNeural": "Chinese — female (edge)",
    "zh-CN-XiaoyiNeural": "Chinese — female (edge)",
    "zh-CN-YunjianNeural": "Chinese — male (edge)",
    "zh-CN-YunxiNeural": "Chinese — male (edge)",
    "zh-CN-YunxiaNeural": "Chinese — male (edge)",
    "zh-CN-YunyangNeural": "Chinese — male, news (edge)",
    "zh-CN-liaoning-XiaobeiNeural": "Chinese — female, Liaoning (edge)",
    "zh-CN-shaanxi-XiaoniNeural": "Chinese — female, Shaanxi (edge)",
    # English (US)
    "en-US-AriaNeural": "English (US) — female (edge)",
    "en-US-AnaNeural": "English (US) — female, child (edge)",
    "en-US-ChristopherNeural": "English (US) — male (edge)",
    "en-US-EricNeural": "English (US) — male (edge)",
    "en-US-GuyNeural": "English (US) — male (edge)",
    "en-US-JennyNeural": "English (US) — female (edge)",
    "en-US-MichelleNeural": "English (US) — female (edge)",
    "en-US-RogerNeural": "English (US) — male (edge)",
    "en-US-SteffanNeural": "English (US) — male (edge)",
    # English (GB)
    "en-GB-LibbyNeural": "English (GB) — female (edge)",
    "en-GB-MaisieNeural": "English (GB) — female, child (edge)",
    "en-GB-RyanNeural": "English (GB) — male (edge)",
    "en-GB-SoniaNeural": "English (GB) — female (edge)",
    "en-GB-ThomasNeural": "English (GB) — male (edge)",
    # Japanese
    "ja-JP-NanamiNeural": "Japanese — female (edge)",
    "ja-JP-KeitaNeural": "Japanese — male (edge)",
    # Korean
    "ko-KR-SunHiNeural": "Korean — female (edge)",
    "ko-KR-InJoonNeural": "Korean — male (edge)",
    "ko-KR-HyunsuNeural": "Korean — male (edge)",
    # French
    "fr-FR-DeniseNeural": "French — female (edge)",
    "fr-FR-HenriNeural": "French — male (edge)",
    # German
    "de-DE-KatjaNeural": "German — female (edge)",
    "de-DE-ConradNeural": "German — male (edge)",
    # Spanish (Spain)
    "es-ES-ElviraNeural": "Spanish (Spain) — female (edge)",
    "es-ES-AlvaroNeural": "Spanish (Spain) — male (edge)",
}

PIPER_VOICES: dict[str, str] = {
    "vi_VN-vais1000-medium": "Vietnamese — piper local (medium)",
    "zh_CN-huayan-medium": "Chinese — piper local (medium)",
}

#: Voice metadata for the Voice Library. Gender/age come from the provider's
#: public voice catalogue (Microsoft neural voices); ``age`` is only set for
#: the documented child voices. Style ``tags`` are only set where the provider
#: documents a style for that voice (news/chat) — everything else stays empty
#: ("Not specified" in the UI). Nothing here is invented.
VOICE_META: dict[str, dict[str, object]] = {
    # Vietnamese
    "vi-VN-HoaiMyNeural": {"lang": "vi", "gender": "female"},
    "vi-VN-NamMinhNeural": {"lang": "vi", "gender": "male"},
    # Chinese (Mandarin)
    "zh-CN-XiaoxiaoNeural": {"lang": "zh", "gender": "female", "tags": ["Chat"]},
    "zh-CN-XiaoyiNeural": {"lang": "zh", "gender": "female"},
    "zh-CN-YunjianNeural": {"lang": "zh", "gender": "male"},
    "zh-CN-YunxiNeural": {"lang": "zh", "gender": "male", "tags": ["Chat"]},
    "zh-CN-YunxiaNeural": {"lang": "zh", "gender": "male"},
    "zh-CN-YunyangNeural": {"lang": "zh", "gender": "male", "tags": ["News", "Narrator"]},
    "zh-CN-liaoning-XiaobeiNeural": {"lang": "zh", "gender": "female"},
    "zh-CN-shaanxi-XiaoniNeural": {"lang": "zh", "gender": "female"},
    # English (US)
    "en-US-AriaNeural": {"lang": "en", "gender": "female", "tags": ["Chat", "Narration"]},
    "en-US-AnaNeural": {"lang": "en", "gender": "female", "age": "child"},
    "en-US-ChristopherNeural": {"lang": "en", "gender": "male"},
    "en-US-EricNeural": {"lang": "en", "gender": "male"},
    "en-US-GuyNeural": {"lang": "en", "gender": "male", "tags": ["Chat"]},
    "en-US-JennyNeural": {"lang": "en", "gender": "female", "tags": ["Chat"]},
    "en-US-MichelleNeural": {"lang": "en", "gender": "female", "tags": ["Chat"]},
    "en-US-RogerNeural": {"lang": "en", "gender": "male", "tags": ["Chat"]},
    "en-US-SteffanNeural": {"lang": "en", "gender": "male", "tags": ["Chat"]},
    # English (GB)
    "en-GB-LibbyNeural": {"lang": "en", "gender": "female"},
    "en-GB-MaisieNeural": {"lang": "en", "gender": "female", "age": "child"},
    "en-GB-RyanNeural": {"lang": "en", "gender": "male"},
    "en-GB-SoniaNeural": {"lang": "en", "gender": "female"},
    "en-GB-ThomasNeural": {"lang": "en", "gender": "male"},
    # Japanese
    "ja-JP-NanamiNeural": {"lang": "ja", "gender": "female", "tags": ["Chat"]},
    "ja-JP-KeitaNeural": {"lang": "ja", "gender": "male"},
    # Korean
    "ko-KR-SunHiNeural": {"lang": "ko", "gender": "female", "tags": ["Chat"]},
    "ko-KR-InJoonNeural": {"lang": "ko", "gender": "male"},
    "ko-KR-HyunsuNeural": {"lang": "ko", "gender": "male"},
    # French
    "fr-FR-DeniseNeural": {"lang": "fr", "gender": "female"},
    "fr-FR-HenriNeural": {"lang": "fr", "gender": "male"},
    # German
    "de-DE-KatjaNeural": {"lang": "de", "gender": "female"},
    "de-DE-ConradNeural": {"lang": "de", "gender": "male"},
    # Spanish (Spain)
    "es-ES-ElviraNeural": {"lang": "es", "gender": "female"},
    "es-ES-AlvaroNeural": {"lang": "es", "gender": "male"},
    # Piper (local) — piper does not expose gender/age; keep honest.
    "vi_VN-vais1000-medium": {"lang": "vi"},
    "zh_CN-huayan-medium": {"lang": "zh"},
}

#: Per-language default preview sentence (real text used for voice previews).
PREVIEW_TEXTS: dict[str, str] = {
    "vi": "Xin chào, đây là bản thử giọng lồng tiếng của video.",
    "en": "Hello, this is a voice preview for your video.",
    "zh": "你好，这是你的视频配音试听。",
    "ja": "こんにちは、これはビデオの吹き替えボイスのプレビューです。",
    "ko": "안녕하세요, 이것은 비디오 더빙 음성 미리보기입니다.",
    "fr": "Bonjour, ceci est un aperçu vocal pour votre vidéo.",
    "de": "Hallo, dies ist eine Sprachvorschau für dein Video.",
    "es": "Hola, esta es una vista previa de voz para tu video.",
}


def voice_meta(engine: str, voice: str) -> dict[str, object]:
    """Voice Library metadata for one voice (honest — "Not specified" when the
    provider does not expose it)."""
    raw = VOICE_META.get(voice, {})
    lang = str(raw.get("lang", ""))
    return {
        "language": lang,
        "gender": str(raw.get("gender", "Not specified")),
        "age": str(raw.get("age", "Not specified")),
        "tags": list(raw.get("tags", [])),
        "preview_text": PREVIEW_TEXTS.get(lang, PREVIEW_TEXTS["en"]),
    }

_PIPER_HF_REPO = "rhasspy/piper-voices"
_PIPER_HF_FILES = {
    "vi_VN-vais1000-medium": (
        "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx",
        "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium.onnx.json",
    ),
    "zh_CN-huayan-medium": (
        "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
        "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
    ),
}

#: Default voice per target language + engine (used when ``voice`` is unset).
#:
#: An engine only lists a language it actually has a native voice for — piper
#: currently ships Vietnamese + Chinese models only, so en/ja/ko deliberately
#: have **no** piper default. Requesting piper for an unsupported language must
#: fail loudly (``E_TTS_UNAVAILABLE``) instead of silently dubbing with a
#: Vietnamese voice (the ``vi_VN-*`` default used before read the target
#: language as en/ja/ko). FIX #4 (review 2026-08-18).
_DEFAULT_VOICE: dict[str, dict[str, str]] = {
    "vi": {ENGINE_EDGE: "vi-VN-HoaiMyNeural", ENGINE_PIPER: "vi_VN-vais1000-medium"},
    "zh": {ENGINE_EDGE: "zh-CN-XiaoxiaoNeural", ENGINE_PIPER: "zh_CN-huayan-medium"},
    "en": {ENGINE_EDGE: "en-US-AriaNeural"},
    "ja": {ENGINE_EDGE: "ja-JP-NanamiNeural"},
    "ko": {ENGINE_EDGE: "ko-KR-SunHiNeural"},
    "fr": {ENGINE_EDGE: "fr-FR-DeniseNeural"},
    "de": {ENGINE_EDGE: "de-DE-KatjaNeural"},
    "es": {ENGINE_EDGE: "es-ES-ElviraNeural"},
}
#: Unknown languages have no default voice for any engine → ``validate_voice``
#: raises instead of silently producing Vietnamese audio.
_DEFAULT_VOICE_FALLBACK: dict[str, str] = {}

#: Progress callback: ``(fraction 0..1)``.
ProgressCallback = Callable[[float], None]


class TTSError(Exception):
    """TTS failure carrying an architecture error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TTSCue:
    """One subtitle cue to speak: seconds + text (translated text)."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TTSCueAudio:
    """One synthesized cue: normalized wav + placement metadata."""

    wav_path: str
    start: float
    end: float
    duration: float
    fitted: bool


@dataclass(frozen=True)
class TTSResult:
    """Output of the TTS stage."""

    voice_track_path: str
    meta_path: str
    engine_used: str
    voice_used: str


def available_engines() -> tuple[str, ...]:
    """Engines whose package is importable (edge-tts / piper)."""
    result: list[str] = []
    try:
        import edge_tts  # noqa: PLC0415, F401

        result.append(ENGINE_EDGE)
    except ImportError:
        pass
    try:
        import piper  # noqa: PLC0415, F401

        result.append(ENGINE_PIPER)
    except ImportError:
        pass
    return tuple(result)


def voice_label(engine: str, voice: str) -> str:
    table = EDGE_VOICES if engine == ENGINE_EDGE else PIPER_VOICES
    return table.get(voice, voice)


def validate_voice(engine: str, voice: str | None, language: str | None) -> tuple[str, str]:
    """Resolve the engine + voice; raises ``TTSError`` when unusable."""
    if engine not in _VALID_ENGINES:
        raise TTSError(E_TTS_UNAVAILABLE, f"Unsupported TTS engine: {engine!r}.")
    if engine == ENGINE_PIPER:
        try:
            import piper  # noqa: PLC0415, F401
        except ImportError as exc:
            raise TTSError(E_TTS_UNAVAILABLE, "piper is not installed; run `pip install piper-tts`.") from exc
    resolved = voice
    if not resolved:
        lang = (language or "").lower()
        resolved = _DEFAULT_VOICE.get(lang, _DEFAULT_VOICE_FALLBACK).get(engine)
        if resolved is None:
            raise TTSError(
                E_TTS_UNAVAILABLE,
                f"{engine} has no default voice for language {language or 'any'}.",
            )
    table = EDGE_VOICES if engine == ENGINE_EDGE else PIPER_VOICES
    if resolved not in table:
        raise TTSError(E_TTS_UNAVAILABLE, f"Unknown {engine} voice: {resolved!r}.")
    return engine, resolved


# ---------------------------------------------------------------------------
# Engine runners (lazy imports; each returns a normalized 44.1k mono wav)
# ---------------------------------------------------------------------------


def _run_ffmpeg(args: list[str], *, stage: str) -> None:
    args = [resolve_ffmpeg(), *args]
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise TTSError(E_TTS_FAILED, f"ffmpeg not found while {stage}.") from exc
    if proc.returncode != 0:
        raise TTSError(
            E_TTS_FAILED,
            f"ffmpeg failed while {stage}: {(proc.stderr or '').strip()[-400:]}",
        )


def _normalize_to_wav(src: str, dst: str, *, atempo: float | None = None) -> float:
    """Convert ``src`` to a 44.1 kHz mono 16-bit WAV; returns duration (s)."""
    args = ["-y", "-nostdin", "-i", src]
    if atempo is not None and abs(atempo - 1.0) > 1e-6:
        args += ["-filter:a", f"atempo={atempo:.4f}"]
    args += [
        "-ac", str(TRACK_CHANNELS),
        "-ar", str(TRACK_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        "-vn",
        dst,
    ]
    _run_ffmpeg(args, stage="audio normalization")
    with wave.open(dst, "rb") as w:
        return w.getnframes() / float(w.getframerate())


#: edge-tts is a free cloud service that intermittently answers a request
#: with metadata but no audio (NoAudioReceived) — observed under rapid
#: successive requests and transient service hiccups. A short retry-with-
#: backoff turns those blips into successes instead of aborting the whole
#: dubbing stage.
_EDGE_MAX_ATTEMPTS = 3
_EDGE_RETRY_DELAY_S = 1.5


def _synthesize_edge(text: str, voice: str, out_wav: str) -> float:
    import asyncio
    import time

    import edge_tts  # noqa: PLC0415 - lazy, heavy

    mp3 = out_wav + ".mp3"
    last_exc: Exception | None = None
    for attempt in range(1, _EDGE_MAX_ATTEMPTS + 1):
        try:
            asyncio.run(edge_tts.Communicate(text, voice).save(mp3))
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 - network/auth/voice failures
            last_exc = exc
            if attempt < _EDGE_MAX_ATTEMPTS:
                logger.warning(
                    "edge-tts attempt %d/%d failed for %r: %s — retrying",
                    attempt,
                    _EDGE_MAX_ATTEMPTS,
                    text[:60],
                    exc,
                )
                time.sleep(_EDGE_RETRY_DELAY_S)
    if last_exc is not None:
        raise TTSError(E_TTS_FAILED, f"edge-tts synthesis failed: {last_exc}") from last_exc
    try:
        return _normalize_to_wav(mp3, out_wav)
    finally:
        try:
            Path(mp3).unlink(missing_ok=True)
        except OSError:
            pass


def _piper_model_path(voice: str) -> tuple[str, str]:
    """Resolve (or download once) the piper ONNX model + config for ``voice``."""
    files = _PIPER_HF_FILES.get(voice)
    if not files:
        raise TTSError(E_TTS_UNAVAILABLE, f"Unknown piper voice: {voice!r}.")
    model_dir = Path(os.environ.get("TTS_MODEL_DIR") or Path.home() / ".cache" / "piper-voices")
    onnx_path = model_dir / Path(files[0]).name
    json_path = model_dir / Path(files[1]).name
    if not (onnx_path.is_file() and json_path.is_file()):
        try:
            from huggingface_hub import hf_hub_download  # noqa: PLC0415 - lazy
        except ImportError as exc:
            raise TTSError(E_TTS_UNAVAILABLE, "huggingface_hub is required to download the piper voice.") from exc
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
            for remote, local in ((files[0], onnx_path), (files[1], json_path)):
                downloaded = hf_hub_download(_PIPER_HF_REPO, remote, local_dir=model_dir)
                if Path(downloaded) != local and not local.is_file():
                    Path(downloaded).rename(local)
        except Exception as exc:  # noqa: BLE001 - network/disk failures
            raise TTSError(E_TTS_FAILED, f"Failed to download piper voice `{voice}`: {exc}") from exc
    return str(onnx_path), str(json_path)


def _synthesize_piper(text: str, voice: str, out_wav: str) -> float:
    from piper import PiperVoice  # noqa: PLC0415 - lazy, heavy

    model_path, _json = _piper_model_path(voice)
    try:
        v = PiperVoice.load(model_path)
        synthesize_wav = getattr(v, "synthesize_wav", None)
        if synthesize_wav is not None:
            # New chunk-based piper API (>=1.6): writes the WAV header itself.
            with wave.open(out_wav, "wb") as wav_file:
                synthesize_wav(text, wav_file)
        else:
            # Legacy rhasspy API: synthesize(text, binary_file).
            with open(out_wav, "wb") as sink:
                v.synthesize(text, sink)
    except Exception as exc:  # noqa: BLE001 - model load/synthesis failures
        raise TTSError(E_TTS_FAILED, f"piper synthesis failed: {exc}") from exc
    tmp = out_wav + ".norm.wav"
    _normalize_to_wav(out_wav, tmp)
    # Install the normalized file over the raw piper output.
    Path(tmp).replace(out_wav)
    with wave.open(out_wav, "rb") as w:
        return w.getnframes() / float(w.getframerate())


# ---------------------------------------------------------------------------
# Single-clip preview synthesis (Voice Library ▶ Preview)
# ---------------------------------------------------------------------------


def synthesize_preview(
    voice: str,
    *,
    engine: str,
    text: str,
    out_wav: str,
) -> float:
    """Synthesize one short clip for the Voice Library preview; returns duration (s).

    Real synthesis through the same engine as dubbing (edge-tts / piper) —
    never a fake audio. The caller is responsible for caching (the API layer
    reuses an existing file for the same engine+voice+text).
    """
    _engine, resolved = validate_voice(engine, voice, None)
    synth = _synthesize_edge if _engine == ENGINE_EDGE else _synthesize_piper
    return synth(text, resolved, out_wav)


# ---------------------------------------------------------------------------
# Cue synthesis + track assembly
# ---------------------------------------------------------------------------


def _fit_atempo(speech_duration: float, window: float) -> float | None:
    """Speed factor so speech fits the cue window (None when it already fits)."""
    if window <= 0.5 or speech_duration <= window:
        return None
    atempo = speech_duration / window
    return min(atempo, MAX_FIT_ATEMPO)


def synthesize_cues(
    cues: list[TTSCue],
    *,
    voice: str | None,
    engine: str,
    language: str | None,
    duration_seconds: float,
    output_dir: str,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
) -> TTSResult:
    """Synthesize each cue and assemble a full-duration voice track."""
    engine, resolved_voice = validate_voice(engine, voice, language)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    synth = _synthesize_edge if engine == ENGINE_EDGE else _synthesize_piper
    cue_audios: list[TTSCueAudio] = []
    texts: list[str] = []
    for idx, cue in enumerate(cues):
        if cancel is not None and cancel.is_cancelled():
            raise CancelledError("TTS cancelled before it started")
        wav_path = out / f"cue_{idx:05d}.wav"
        window = cue.end - cue.start
        natural = synth(cue.text, resolved_voice, str(wav_path))
        fit = _fit_atempo(natural, window)
        if fit is not None and abs(fit - 1.0) > 1e-6:
            fitted_path = str(wav_path) + ".fit.wav"
            duration = _normalize_to_wav(str(wav_path), fitted_path, atempo=fit)
            # Replace the original cue wav with the time-fitted one.
            Path(fitted_path).replace(wav_path)
        else:
            duration = natural
        cue_audios.append(
            TTSCueAudio(
                wav_path=str(wav_path),
                start=cue.start,
                end=cue.end,
                duration=duration,
                fitted=fit is not None,
            )
        )
        texts.append(cue.text)
        if cancel is not None and cancel.is_cancelled():
            raise CancelledError("TTS cancelled mid-synthesis")
        if on_progress is not None:
            on_progress((idx + 1) / len(cues))

    track_path = str(out / "voice_track.wav")
    meta_path = str(out / "tts_meta.json")
    _assemble_track(cue_audios, duration_seconds, track_path)
    meta = {
        "schema_version": 1,
        "engine": engine,
        "voice": resolved_voice,
        "duration_seconds": duration_seconds,
        "cues": [
            {
                "index": i,
                "text": texts[i],
                "start": c.start,
                "end": c.end,
                "duration": round(c.duration, 3),
                "fitted": c.fitted,
                "wav": c.wav_path,
            }
            for i, c in enumerate(cue_audios)
        ],
    }
    Path(meta_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("TTS done: engine=%s voice=%s cues=%d track=%s", engine, resolved_voice, len(cue_audios), track_path)
    return TTSResult(
        voice_track_path=track_path,
        meta_path=meta_path,
        engine_used=engine,
        voice_used=resolved_voice,
    )


def _assemble_track(cue_audios: list[TTSCueAudio], duration_seconds: float, out_path: str) -> None:
    """Place each cue's speech at its start into a silent full-duration track."""
    total_frames = max(1, int(duration_seconds * TRACK_SAMPLE_RATE))
    buf = bytearray(total_frames * TRACK_CHANNELS * TRACK_SAMPLE_WIDTH)
    for audio in cue_audios:
        with wave.open(audio.wav_path, "rb") as w:
            if w.getframerate() != TRACK_SAMPLE_RATE or w.getnchannels() != TRACK_CHANNELS:
                raise TTSError(E_TTS_FAILED, "internal: cue wav not normalized")
            frames = w.readframes(w.getnframes())
        # Byte offset = frame index * sample width (mono 16-bit => *2). Using
        # frame indices as byte offsets shifts every later cue earlier.
        start_byte = int(audio.start * TRACK_SAMPLE_RATE) * TRACK_SAMPLE_WIDTH
        if start_byte >= len(buf):
            continue
        end_byte = min(start_byte + len(frames), len(buf))
        buf[start_byte:end_byte] = frames[: end_byte - start_byte]
    with wave.open(out_path, "wb") as w:
        w.setnchannels(TRACK_CHANNELS)
        w.setsampwidth(TRACK_SAMPLE_WIDTH)
        w.setframerate(TRACK_SAMPLE_RATE)
        w.writeframes(bytes(buf))
