"""STTService (TASK-013): transcribe audio with faster-whisper.

Frozen strategy (ARCHITECTURE_DECISION.md §2.4 / §3.2): faster-whisper is the
primary engine (CUDA/CPU, int8). TASK-014 adds full GPU detection and TASK-015
adds the whisper.cpp fallback; this module only needs a resolved ``device``.

Design
------
- **Lazy model load**: ``faster_whisper`` is imported only inside
  ``_load_whisper_model`` so this module imports cleanly (and tests run) even
  where the heavy AI stack is absent.
- **Injectable model**: ``transcribe(..., whisper_model=...)`` accepts a
  pre-loaded model (a real ``WhisperModel`` or a test double with the same
  ``transcribe(audio, ...) -> (segments, info)`` contract), so the pipeline
  logic is unit-tested without a model.
- **Silero VAD**: delegated to faster-whisper's built-in ``vad_filter=True``.
- **Progress / cancel**: segments are consumed lazily from the generator;
  ``on_progress(ratio)`` fires per segment (time-based against the audio
  duration) and cancellation is checked between segments.
- **VRAM guard**: before loading on CUDA, the requested model is downgraded to
  the largest tier that fits the free VRAM (large-v3 -> turbo -> small -> base
  -> tiny), falling back to CPU-appropriate tiers. Full VRAM strategy lands in
  TASK-014; this is the MVP guard the DoD requires.

Error codes follow the ``E_<MODULE>_FAILED`` pattern of MASTER_PLAN §28.1:

- ``E_STT_MODEL_UNAVAILABLE`` — faster-whisper is not installed.
- ``E_STT_FAILED``          — model load or transcription failed.
- ``E_STT_NO_SPEECH``       — no transcribable speech produced any segment.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Callable

from src.core.job import CancelledError, CancellationToken

logger = logging.getLogger(__name__)

E_STT_MODEL_UNAVAILABLE = "E_STT_MODEL_UNAVAILABLE"
E_STT_FAILED = "E_STT_FAILED"
E_STT_NO_SPEECH = "E_STT_NO_SPEECH"

#: Approximate int8 VRAM footprint per model (MASTER_PLAN §5 / §14.2).
MODEL_VRAM_REQUIREMENTS_MB: dict[str, float] = {
    "large-v3": 2900.0,
    "turbo": 2500.0,
    "small": 1200.0,
    "base": 700.0,
    "tiny": 400.0,
}

#: Downgrade path when VRAM is short (largest-to-smallest).
_MODEL_DOWNGRADE_ORDER = ("turbo", "small", "base", "tiny")

#: Progress callback: ``(fraction 0..1)`` of transcription.
ProgressCallback = Callable[[float], None]

_VALID_DEVICES = ("auto", "cuda", "cpu")


class STTError(Exception):
    """STT failure carrying an architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TranscribeResult:
    """Output of the STT stage plus the effective model/device used."""

    transcript: dict[str, Any]
    model_used: str
    device_used: str


def resolve_device(requested: str) -> str:
    """Resolve ``auto`` to ``cuda``/``cpu`` (lazy torch import)."""
    if requested not in _VALID_DEVICES:
        raise STTError(E_STT_FAILED, f"Unsupported device: {requested!r}.")
    if requested != "auto":
        return requested
    try:
        import torch  # noqa: PLC0415 - lazy, heavy

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def pick_compute_type(device: str) -> str:
    """int8 for CPU, int8_float16 for CUDA (ARCHITECTURE_DECISION.md §3.2)."""
    return "int8_float16" if device == "cuda" else "int8"


def available_vram_mb() -> float | None:
    """Free VRAM in MB on the current CUDA device, or ``None`` if unknown."""
    try:
        import torch  # noqa: PLC0415 - lazy, heavy

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return free / (1024 * 1024)
    except (ImportError, RuntimeError):
        return None


def guard_model_tier(requested_model: str, available_vram_mb: float | None) -> str:
    """Downgrade ``requested_model`` to the largest tier that fits VRAM.

    Returns ``requested_model`` unchanged when VRAM is unknown or sufficient
    (MASTER_PLAN §14.2 VRAM guard: below ~2.5 GB use turbo/small).
    """
    required = MODEL_VRAM_REQUIREMENTS_MB.get(
        requested_model, MODEL_VRAM_REQUIREMENTS_MB["large-v3"]
    )
    if available_vram_mb is None or available_vram_mb >= required:
        return requested_model
    for candidate in _MODEL_DOWNGRADE_ORDER:
        if available_vram_mb >= MODEL_VRAM_REQUIREMENTS_MB[candidate]:
            return candidate
    return "tiny"


def _load_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    """Lazy-import faster-whisper and load ``model_name`` on ``device``."""
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 - lazy, heavy
    except ImportError as exc:
        raise STTError(
            E_STT_MODEL_UNAVAILABLE,
            "faster-whisper is not installed; cannot run STT.",
        ) from exc
    try:
        return WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 - map every load failure to a code
        lowered = str(exc).lower()
        if device == "cuda" and any(m in lowered for m in ("memory", "vram")):
            raise STTError(E_STT_FAILED, "Not enough VRAM to load the STT model.") from exc
        raise STTError(E_STT_FAILED, "Failed to load the STT model.") from exc


def _segment_language(segment: Any, fallback: str) -> str:
    lang = getattr(segment, "language", None)
    return lang or fallback


def _segment_confidence(segment: Any) -> float:
    avg_logprob = getattr(segment, "avg_logprob", None)
    if avg_logprob is None:
        return 0.0
    try:
        return round(max(0.0, min(1.0, math.exp(float(avg_logprob)))), 4)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def build_transcript(
    segments: Any,
    *,
    project_id: str,
    model_name: str,
    language_override: str | None,
    detected_language: str | None = None,
    total_duration_seconds: float | None = None,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Consume a faster-whisper segment generator into a canonical Transcript
    (schemas/transcript.schema.json §24.1).

    - Cancellation is checked between segments (per TASK-013 DoD).
    - Empty segments are dropped (the schema requires ``text`` >= 1 char).
    - ``on_progress(ratio)`` maps the running segment end time to 0..1.
    """
    resolved_language = language_override or detected_language or "und"
    segments_out: list[dict[str, Any]] = []
    for idx, segment in enumerate(segments):
        if cancel is not None and cancel.is_cancelled():
            raise CancelledError("transcription cancelled")
        text = (getattr(segment, "text", None) or "").strip()
        if not text:
            continue
        start = max(0.0, float(getattr(segment, "start", 0.0) or 0.0))
        end = max(start, float(getattr(segment, "end", start) or start))
        segments_out.append(
            {
                "id": f"seg_{idx}",
                "idx": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "language": _segment_language(segment, resolved_language),
                "confidence": _segment_confidence(segment),
            }
        )
        if on_progress is not None and total_duration_seconds and total_duration_seconds > 0:
            on_progress(max(0.0, min(1.0, end / total_duration_seconds)))

    if not segments_out:
        raise STTError(E_STT_NO_SPEECH, "No speech detected in the audio.")

    return {
        "schema_version": 1,
        "project_id": project_id,
        "language": resolved_language,
        "model": model_name,
        "segments": segments_out,
    }


def transcribe(
    audio_path: str,
    *,
    project_id: str,
    model_name: str = "large-v3",
    device: str = "auto",
    compute_type: str | None = None,
    language: str | None = None,
    total_duration_seconds: float | None = None,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
    whisper_model: Any | None = None,
) -> TranscribeResult:
    """Transcribe ``audio_path`` into a canonical Transcript.

    ``whisper_model`` is a test seam: when provided the guard/load steps are
    skipped and it is used as-is (it must expose ``transcribe(audio, ...)``
    returning ``(segments, info)`` like faster-whisper 1.x).
    """
    if not os.path.isfile(audio_path):
        raise STTError(E_STT_FAILED, "Audio file does not exist.")
    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("transcription cancelled before it started")

    resolved_device = resolve_device(device)
    resolved_compute = compute_type or pick_compute_type(resolved_device)

    if whisper_model is not None:
        model = whisper_model
        effective_model = model_name
    else:
        vram = available_vram_mb() if resolved_device == "cuda" else None
        effective_model = guard_model_tier(model_name, vram)
        if effective_model != model_name:
            logger.warning(
                "VRAM guard: downgraded STT model %s -> %s (free VRAM %.0f MB)",
                model_name,
                effective_model,
                vram or 0.0,
            )
        model = _load_whisper_model(effective_model, resolved_device, resolved_compute)

    try:
        result = model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - map every transcription failure
        raise STTError(E_STT_FAILED, "Transcription failed.") from exc

    if isinstance(result, tuple):
        segments, info = result
    else:
        segments, info = result, None

    detected = getattr(info, "language", None) if info is not None else None
    duration = total_duration_seconds
    if duration is None and info is not None:
        info_duration = getattr(info, "duration", None)
        if info_duration is not None:
            duration = float(info_duration)

    transcript = build_transcript(
        segments,
        project_id=project_id,
        model_name=effective_model,
        language_override=language,
        detected_language=detected,
        total_duration_seconds=duration,
        cancel=cancel,
        on_progress=on_progress,
    )
    return TranscribeResult(
        transcript=transcript,
        model_used=effective_model,
        device_used=resolved_device,
    )
