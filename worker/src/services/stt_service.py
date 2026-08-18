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
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from src.core.cuda_libs import ensure_cuda_libraries
from src.core.job import CancelledError, CancellationToken

logger = logging.getLogger(__name__)

E_STT_MODEL_UNAVAILABLE = "E_STT_MODEL_UNAVAILABLE"
E_STT_FAILED = "E_STT_FAILED"
E_STT_NO_SPEECH = "E_STT_NO_SPEECH"

#: TASK-015 mitigation 3: whisper.cpp model spawns are serialized behind this
#: lock so two concurrent STT jobs can never race the ggml/Vulkan init
#: (whisper.cpp issue #3638).
_WHISPER_INIT_LOCK = threading.Lock()

#: Chunked-pipeline shared faster-whisper model cache. Loading large-v3 costs
#: ~3 GB RAM plus a HuggingFace revision round-trip per load; the chunk
#: scheduler runs several chunks concurrently, so each chunk used to load its
#: own copy (OOM / network flakes on long videos). One instance is loaded per
#: (model, device, compute_type) and reused — CTranslate2 inference is safe
#: for concurrent ``transcribe()`` calls on the same model object.
_WHISPER_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_WHISPER_MODEL_LOCK = threading.Lock()

BACKEND_FASTER_WHISPER = "faster-whisper"
BACKEND_WHISPER_CPP = "whisper-cpp"

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

#: Substrings that identify a CUDA runtime-library failure (missing cuBLAS /
#: cuDNN / cudart, unsupported driver). These are recoverable by retrying on
#: CPU — anything else during STT is a genuine failure.
_CUDA_LIB_ERROR_MARKERS = (
    "cublas",
    "cudnn",
    "cublaslt",
    "cudart",
    "is not found",
    "cannot be loaded",
    "cuda error",
    "driver",
)

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
    """Resolve ``auto`` to ``cuda``/``cpu``.

    Prefers torch when installed; without torch, probes CUDA through
    ctranslate2 directly (the same engine faster-whisper uses). Any probe
    failure degrades to CPU — never raises.
    """
    if requested not in _VALID_DEVICES:
        raise STTError(E_STT_FAILED, f"Unsupported device: {requested!r}.")
    if requested != "auto":
        return requested
    try:
        import torch  # noqa: PLC0415 - lazy, heavy

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        pass
    try:
        from ctranslate2 import get_cuda_device_count  # noqa: PLC0415

        ensure_cuda_libraries()
        return "cuda" if get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001 - no CUDA runtime libs / driver: CPU
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


def _is_cuda_library_error(exc: Exception) -> bool:
    """True when a runtime exception indicates a missing/broken CUDA library.

    faster-whisper defers all inference to a lazy generator, so a missing
    ``cublas64_12.dll`` surfaces as a ``RuntimeError`` only when the segments
    generator is consumed. Such failures are recoverable by retrying on CPU;
    this classifier lets ``transcribe`` do exactly that instead of failing the
    job with a 500.
    """
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _CUDA_LIB_ERROR_MARKERS)


def _load_whisper_model(model_name: str, device: str, compute_type: str) -> Any:
    """Lazy-import faster-whisper and load ``model_name`` on ``device``.

    Instances are **shared** (module-level cache keyed by model/device/compute):
    the chunked pipeline runs several chunks concurrently, and loading a fresh
    ~3 GB large-v3 per chunk exhausted RAM and added a HuggingFace revision
    round-trip per load. ``local_files_only`` skips that network check when the
    model is already cached locally (falls back to a normal load otherwise).
    """
    key = (model_name, device, compute_type)
    cached = _WHISPER_MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    with _WHISPER_MODEL_LOCK:
        cached = _WHISPER_MODEL_CACHE.get(key)
        if cached is not None:
            return cached

        # Register pip-provided CUDA DLLs (Windows) before ctranslate2 resolves
        # its dependencies at first encode; harmless elsewhere.
        ensure_cuda_libraries()
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415 - lazy, heavy
        except ImportError as exc:
            raise STTError(
                E_STT_MODEL_UNAVAILABLE,
                "faster-whisper is not installed; cannot run STT.",
            ) from exc
        try:
            try:
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=True,
                )
            except Exception:  # noqa: BLE001 - not cached yet → allow download
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _WHISPER_MODEL_CACHE[key] = model
            return model
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
    backend: str = BACKEND_FASTER_WHISPER,
    strategy: Any | None = None,
    whisper_cli: Callable | None = None,
    model_path: str | None = None,
    num_threads: int | None = None,
    beam_size: int | None = 5,
    no_flash_attn: bool = False,
) -> TranscribeResult:
    """Transcribe ``audio_path`` into a canonical Transcript.

    ``whisper_model`` is a test seam: when provided the guard/load steps are
    skipped and it is used as-is (it must expose ``transcribe(audio, ...)``
    returning ``(segments, info)`` like faster-whisper 1.x).

    ``backend`` selects the engine (TASK-015): ``faster-whisper`` (default),
    ``whisper-cpp`` (AMD/Intel/CPU fallback), or ``auto`` which defers to the
    resolved ``strategy`` (TASK-014): a strategy whose ``stt_backend`` is
    ``whisper-cpp`` routes here. ``whisper_cli`` is a test seam for the
    whisper-cpp runner (same ``(args, *, cancel, on_progress)`` contract).
    """
    if backend == "auto":
        strategy_backend = getattr(strategy, "stt_backend", BACKEND_FASTER_WHISPER)
        backend = BACKEND_WHISPER_CPP if strategy_backend == BACKEND_WHISPER_CPP else BACKEND_FASTER_WHISPER

    if backend == BACKEND_WHISPER_CPP:
        return _transcribe_whisper_cpp(
            audio_path,
            project_id=project_id,
            language=language,
            total_duration_seconds=total_duration_seconds,
            cancel=cancel,
            on_progress=on_progress,
            model_path=model_path,
            num_threads=num_threads,
            beam_size=beam_size,
            no_flash_attn=no_flash_attn,
            whisper_cli=whisper_cli,
        )

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

    def _run_and_build(mdl: Any) -> dict[str, Any]:
        """Transcribe + consume the lazy generator + build the transcript.

        All real inference happens here — faster-whisper returns a generator
        from ``transcribe()`` and encodes lazily as ``build_transcript``
        iterates it, so CUDA runtime errors surface inside this call.
        """
        result = mdl.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        segments, info = result if isinstance(result, tuple) else (result, None)
        detected = getattr(info, "language", None) if info is not None else None
        duration = total_duration_seconds
        if duration is None and info is not None:
            info_duration = getattr(info, "duration", None)
            if info_duration is not None:
                duration = float(info_duration)
        return build_transcript(
            segments,
            project_id=project_id,
            model_name=effective_model,
            language_override=language,
            detected_language=detected,
            total_duration_seconds=duration,
            cancel=cancel,
            on_progress=on_progress,
        )

    try:
        transcript = _run_and_build(model)
    except CancelledError:
        raise
    except STTError:
        raise
    except RuntimeError as exc:
        # CUDA runtime library missing/broken (e.g. cublas64_12.dll absent):
        # retry once on CPU instead of failing the job with a 500.
        if resolved_device != "cuda" or not _is_cuda_library_error(exc):
            raise STTError(E_STT_FAILED, "Transcription failed.") from exc
        logger.warning("CUDA runtime library failure (%s); retrying STT on CPU", exc)
        ensure_cuda_libraries()
        try:
            transcript = _run_and_build(_load_whisper_model(effective_model, "cpu", "int8"))
        except STTError:
            raise
        except Exception as cpu_exc:  # noqa: BLE001 - map CPU fallback failure
            raise STTError(E_STT_FAILED, "Transcription failed (CPU fallback also failed).") from cpu_exc
        return TranscribeResult(
            transcript=transcript,
            model_used=effective_model,
            device_used="cpu",
        )
    except Exception as exc:  # noqa: BLE001 - map every transcription failure
        raise STTError(E_STT_FAILED, "Transcription failed.") from exc
    return TranscribeResult(
        transcript=transcript,
        model_used=effective_model,
        device_used=resolved_device,
    )


def _transcribe_whisper_cpp(
    audio_path: str,
    *,
    project_id: str,
    language: str | None,
    total_duration_seconds: float | None,
    cancel: CancellationToken | None,
    on_progress: ProgressCallback | None,
    model_path: str | None,
    num_threads: int | None,
    beam_size: int | None,
    no_flash_attn: bool,
    whisper_cli: Callable | None,
) -> TranscribeResult:
    """TASK-015: transcribe via the whisper-cli sidecar (Vulkan/CPU fallback).

    Applies the three mandatory mitigations (MASTER_PLAN §14.2):
    1. ``beam_size`` capped at 6 by ``build_transcribe_args``.
    2. ``--no-flash-attn`` when the caller resolved an AMD/Intel Vulkan device.
    3. Model spawn serialized behind ``_WHISPER_INIT_LOCK``.
    """
    from src.core.whisper_cpp import (  # noqa: PLC0415 - lazy sidecar module
        WhisperCppError,
        build_transcribe_args,
        parse_json_output,
        resolve_whisper_cli,
        run_whisper_cli,
    )

    if not os.path.isfile(audio_path):
        raise STTError(E_STT_FAILED, "Audio file does not exist.")
    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("transcription cancelled before it started")
    if not model_path:
        raise STTError(E_STT_FAILED, "whisper-cpp backend requires a model_path.")

    binary = resolve_whisper_cli()
    args = [binary] + build_transcribe_args(
        model_path,
        audio_path,
        language=language,
        num_threads=num_threads,
        beam_size=beam_size,
        no_flash_attn=no_flash_attn,
    )

    with _WHISPER_INIT_LOCK:
        try:
            if whisper_cli is not None:
                result = whisper_cli(args, cancel=cancel, on_progress=on_progress)
            else:
                result = run_whisper_cli(args, cancel=cancel, on_progress=on_progress)
        except CancelledError:
            raise
        except WhisperCppError as exc:
            raise STTError(exc.code, exc.message) from exc
        except Exception as exc:  # noqa: BLE001 - map every runner failure
            raise STTError(E_STT_FAILED, "whisper.cpp transcription failed.") from exc

    if result.returncode != 0:
        raise STTError(E_STT_FAILED, "whisper.cpp transcription failed.")

    parsed = parse_json_output(result.output_json)
    segments = [
        SimpleNamespace(text=seg["text"], start=seg["start"], end=seg["end"])
        for seg in parsed["segments"]
    ]
    transcript = build_transcript(
        segments,
        project_id=project_id,
        model_name="whisper-cpp",
        language_override=language,
        detected_language=parsed.get("language"),
        total_duration_seconds=total_duration_seconds,
        cancel=cancel,
        on_progress=on_progress,
    )
    return TranscribeResult(
        transcript=transcript,
        model_used="whisper-cpp",
        device_used="cpu",
    )
