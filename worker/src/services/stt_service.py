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
#: (model, device, compute_type, cpu_threads) and reused — CTranslate2
#: inference is safe for concurrent ``transcribe()`` calls on the same model.
_WHISPER_MODEL_CACHE: dict[tuple[str, str, str, int | None], Any] = {}
_WHISPER_MODEL_LOCK = threading.Lock()

BACKEND_FASTER_WHISPER = "faster-whisper"
BACKEND_WHISPER_CPP = "whisper-cpp"

#: STT engine selection (BATCHED_STT task). ``auto`` resolves at runtime to
#: ``batched`` only when a CUDA device is present AND the VRAM head-room is
#: safe; otherwise it stays ``regular`` (safe default). Calling ``batched``
#: while ``regular`` still works means a batched failure or an unsafe runtime
#: silently falls back to ``regular`` — never to a half-written transcript.
STT_MODE_AUTO = "auto"
STT_MODE_REGULAR = "regular"
STT_MODE_BATCHED = "batched"
VALID_STT_MODES = (STT_MODE_AUTO, STT_MODE_REGULAR, STT_MODE_BATCHED)

#: Supported ``batch_size`` values for BatchedInferencePipeline (measured:
#: 1/2/4 monotone latency wins on the 4 GB benchmark rig; 4 is NOT the default
#: because mid-RAM GPUs (2 GB) still fit batch=2 comfortably).
DEFAULT_BATCH_SIZE = 2
SUPPORTED_BATCH_SIZES = (1, 2, 4)

#: Conservative VRAM head-room the auto selector requires ON TOP of the model's
#: int8 footprint before it stays ``batched`` (measured: ``small`` single-pass
#: ~482 MB vs batched batch=4 peak ~738 MB, so a fixed margin covers the extra
#: encoder activations + reconstruction buffers for any model).
BATCHED_VRAM_MARGIN_MB = 400.0

#: Reconstruction governs segments AFTER of the batched inference returns coarse
#: 30s window segments: we split raw word timestamps into sentence/phrase
#: segments again (purely offline, no re-inference). Constants are configurable,
#: never experimental conclusions.
RECON_SENTENCE_ENDERS = frozenset(".!?。！？…")
RECON_MAX_SEGMENT_SECONDS = 8.0
RECON_GAP_THRESHOLD_SECONDS = 1.0
RECON_SENTENCE_MIN_CHARS = 8
RECON_MAX_CHARS_FALLBACK = 96

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
    #: Which engine actually produced the transcript (regular vs batched, or
    #: ``batched→regular`` meaning a fallback happened). Needed by the chunk
    #: pipeline's ``[STT]`` automation logging.
    engine: str = STT_MODE_REGULAR


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


def _load_whisper_model(
    model_name: str,
    device: str,
    compute_type: str,
    cpu_threads: int | None = None,
) -> Any:
    """Lazy-import faster-whisper and load ``model_name`` on ``device``.

    Instances are **shared** (module-level cache keyed by model/device/compute/
    cpu_threads): the chunked pipeline runs several chunks concurrently, and
    loading a fresh ~3 GB large-v3 per chunk exhausted RAM and added a
    HuggingFace revision round-trip per load. ``local_files_only`` skips that
    network check when the model is already cached locally (falls back to a
    normal load otherwise).
    """
    key = (model_name, device, compute_type, cpu_threads)
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
            # Only pass cpu_threads when set — leaving it out keeps the
            # faster-whisper default (all cores) for non-chunked callers.
            kwargs = {"cpu_threads": cpu_threads} if cpu_threads is not None else {}
            try:
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=True,
                    **kwargs,
                )
            except Exception:  # noqa: BLE001 - not cached yet → allow download
                model = WhisperModel(model_name, device=device, compute_type=compute_type, **kwargs)
            _WHISPER_MODEL_CACHE[key] = model
            return model
        except Exception as exc:  # noqa: BLE001 - map every load failure to a code
            lowered = str(exc).lower()
            if device == "cuda" and any(m in lowered for m in ("memory", "vram")):
                raise STTError(E_STT_FAILED, "Not enough VRAM to load the STT model.") from exc
            raise STTError(E_STT_FAILED, "Failed to load the STT model.") from exc


def resolve_stt_mode(
    requested: str,
    *,
    device: str,
    model_name: str,
    batch_size: int,
    vram_mb: float | None,
) -> tuple[str, str]:
    """Resolve ``auto`` to ``batched``/``regular`` or validate an explicit mode.

    Returns ``(mode, reason)``. ``auto`` picks ``batched`` only when the three
    constraints hold together: CUDA device present, model supports batching
    (every faster-whisper model here does), and free VRAM has comfortable
    head-room over the model's footprint. Any miss → ``regular`` (safe default).
    """
    if requested not in VALID_STT_MODES:
        raise STTError(E_STT_FAILED, f"Unsupported stt_mode: {requested!r}.")
    if batch_size not in SUPPORTED_BATCH_SIZES:
        raise STTError(
            E_STT_FAILED,
            f"Unsupported batch_size {batch_size!r}; must be one of {SUPPORTED_BATCH_SIZES}.",
        )
    if requested == STT_MODE_REGULAR:
        return STT_MODE_REGULAR, "explicit regular"
    if requested == STT_MODE_BATCHED:
        return STT_MODE_BATCHED, "explicit batched"
    # --- auto --------------------------------------------------------------
    if device != "cuda":
        return STT_MODE_REGULAR, f"auto → regular (device={device} not cuda)"
    if vram_mb is None:
        return STT_MODE_REGULAR, "auto → regular (free VRAM unknown)"
    required = MODEL_VRAM_REQUIREMENTS_MB.get(
        model_name, MODEL_VRAM_REQUIREMENTS_MB["large-v3"]
    )
    headroom = required + BATCHED_VRAM_MARGIN_MB
    if vram_mb < headroom:
        return (
            STT_MODE_REGULAR,
            f"auto → regular (VRAM {vram_mb:.0f}MB < required {required:.0f}MB + margin {BATCHED_VRAM_MARGIN_MB:.0f}MB)",
        )
    return STT_MODE_BATCHED, "auto → batched (cuda + VRAM safe)"


def validate_segment_timestamps(
    segments: list[dict[str, Any]], *, tolerance_s: float = 0.001
) -> list[str]:
    """Post-condition the batched engine MUST satisfy before a transcript is
    accepted (RECON/fallback contract). Returns issues, not raises.

    Enforces (without inventing repairs): monotonic ``start``, ``end >= start``,
    no negative timestamps, no duplicate segments, no unexplained overlaps.
    Overlaps within ``tolerance_s`` are accepted (rounding at reconstruct
    boundaries); real overlaps that hide dropped/corrupted words are issues.
    """
    issues: list[str] = []
    prev_start: float | None = None
    prev_end: float | None = None
    seen: set[tuple[float, float, str]] = set()
    for i, seg in enumerate(segments):
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", start) or start)
        if start < 0.0 or end < 0.0:
            issues.append(f"segment {i}: negative timestamp (start={start}, end={end})")
        if end < start:
            issues.append(f"segment {i}: end {end} < start {start}")
        key = (round(start, 2), round(end, 2), str(seg.get("text", "")))
        if key in seen:
            issues.append(f"segment {i}: duplicate [{key}]")
        else:
            seen.add(key)
        if prev_start is not None and start < prev_start - tolerance_s:
            issues.append(f"segment {i}: non-monotonic start {start} < {prev_start}")
        if prev_end is not None and start < prev_end - tolerance_s:
            issues.append(f"segment {i}: overlap start {start} < prev_end {prev_end}")
        prev_start = start
        prev_end = max(prev_end or 0.0, end)
    return issues


def _char_cap(language: str) -> int:
    from src.services.subtitle_service import default_style  # noqa: PLC0415 - lazy

    try:
        return default_style(language).max_chars_per_line * 2
    except Exception:  # noqa: BLE001 - conservative fallback cap
        return RECON_MAX_CHARS_FALLBACK


def _flatten_word_timestamps(segments: Any) -> list[dict[str, Any]]:
    """Flatten batched segment word timestamps into a sorted word list.

    Preserves every word's start/end/probability; sorting is by ``(start, end)``
    so downstream reconstruction is deterministic regardless of segment order.
    """
    out: list[dict[str, Any]] = []
    for seg in segments:
        for w in list(getattr(seg, "words", None) or []):
            start = float(getattr(w, "start", 0.0) or 0.0)
            end = float(getattr(w, "end", start) or start)
            text = (getattr(w, "word", "") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "start": start,
                    "end": max(start, end),
                    "text": text,
                    "probability": getattr(w, "probability", None),
                }
            )
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def reconstruct_segments_from_words(
    words: list[dict[str, Any]],
    *,
    language: str,
) -> list[dict[str, Any]]:
    """Offline rule-based reconstruction of sentence/phrase segments from the
    batched pipeline's word timestamps (no re-inference).

    Splits at (1) sentence punctuation, (2) a real inter-word silence gap, and
    caps (3) span seconds and (4) char count. Pure function over the word list
    — deterministic, unit-testable, and produces the same normalized segment
    shape (start/end/text/language/confidence/words) as single-pass output.
    """
    cap = _char_cap(language)
    segments_out: list[dict[str, Any]] = []
    cur_words: list[dict[str, Any]] = []
    cur_text = ""
    cur_start: float | None = None
    cur_end: float | None = None

    def flush() -> None:
        nonlocal cur_words, cur_text, cur_start, cur_end
        if not cur_words:
            return
        seg_words = list(cur_words)
        first, last = seg_words[0], seg_words[-1]
        probs = [w["probability"] for w in seg_words if w["probability"] is not None]
        conf = round(sum(probs) / len(probs), 4) if probs else 0.0
        start = max(0.0, float(cur_start or first["start"]))
        end = max(start, float(cur_end or last["end"]))
        segments_out.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": cur_text,
                "language": language,
                "confidence": conf,
                "words": [
                    {
                        "word": w["text"],
                        "start": round(max(start, w["start"]), 3),
                        "end": round(min(end, w["end"]), 3),
                        "probability": w["probability"],
                    }
                    for w in seg_words
                ],
            }
        )
        cur_words = []
        cur_text = ""
        cur_start = None
        cur_end = None

    for w in words:
        text = (w["text"] or "").strip()
        if not text:
            continue
        start = float(w["start"])
        end = max(start, float(w["end"]))
        if cur_words:
            # Split on (2) a real silence gap or (3) a hard cap — both are
            # evaluated *before* appending so the overflowing word starts the
            # next segment instead of being swallowed by the current one.
            gap = start - (cur_end if cur_end is not None else start)
            prospective_span = end - (cur_start if cur_start is not None else start)
            prospective_chars = len(cur_text) + 1 + len(text)
            if (
                gap > RECON_GAP_THRESHOLD_SECONDS
                or prospective_span > RECON_MAX_SEGMENT_SECONDS
                or prospective_chars > cap
            ):
                flush()
        if cur_words:
            cur_text += " "
        cur_text += text
        cur_words.append(w)
        if cur_start is None:
            cur_start = start
        cur_end = max(cur_end if cur_end is not None else start, end)
        # Split at (1) sentence punctuation.
        is_sentence_end = text.endswith(tuple(RECON_SENTENCE_ENDERS))
        if is_sentence_end and len(cur_text) >= RECON_SENTENCE_MIN_CHARS:
            flush()

    flush()
    return segments_out


class STTEngine:
    """Base contract shared by every STT engine.

    Every engine normalizes its raw inference output into the SAME transcript
    shape (via ``build_transcript``), so Translation/TTS/Subtitle/Assembly never
    learn *which* engine produced a segment (BATCHED_STT task contract).
    """

    name: str = STT_MODE_REGULAR

    def transcribe(
        self,
        model: Any,
        audio_path: str,
        *,
        project_id: str,
        model_name: str,
        language: str | None,
        total_duration_seconds: float | None,
        cancel: CancellationToken | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    # -- shared helpers ------------------------------------------------------

    @staticmethod
    def _consume(result: Any) -> tuple[list[Any], Any]:
        segments, info = result if isinstance(result, tuple) else (result, None)
        return list(segments), info

    @staticmethod
    def _build(
        segments: list[Any],
        info: Any,
        *,
        project_id: str,
        model_name: str,
        language: str | None,
        total_duration_seconds: float | None,
        cancel: CancellationToken | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        detected = getattr(info, "language", None) if info is not None else None
        duration = total_duration_seconds
        if duration is None and info is not None:
            info_duration = getattr(info, "duration", None)
            if info_duration is not None:
                duration = float(info_duration)
        return build_transcript(
            segments,
            project_id=project_id,
            model_name=model_name,
            language_override=language,
            detected_language=detected,
            total_duration_seconds=duration,
            cancel=cancel,
            on_progress=on_progress,
        )


class RegularSTTEngine(STTEngine):
    """The existing production engine: single-pass faster-whisper
    ``transcribe(vad_filter=True, beam_size=5)`` producing sentence segments.

    This is the untouched baseline — identical behavior to what shipped before
    the batched engine existed (backwards compatibility is a hard DoD).
    """

    name = STT_MODE_REGULAR

    def transcribe(self, model, audio_path, *, project_id, model_name, language, total_duration_seconds, cancel, on_progress):
        result = model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )
        segments, info = self._consume(result)
        return self._build(
            segments,
            info,
            project_id=project_id,
            model_name=model_name,
            language=language,
            total_duration_seconds=total_duration_seconds,
            cancel=cancel,
            on_progress=on_progress,
        )


class BatchedSTTEngine(STTEngine):
    """batched faster-whisper ``BatchedInferencePipeline`` + offline word-time
    reconstruction into the same normalized segment contract as regular mode.

    ``beam_size=5`` and ``vad_filter=True`` mirror the regular engine; batch
    size is 1/2/4. Raw 30s-window segments are re-split on word timestamps, then
    validated before acceptance (invalid output → caller falls back to regular).
    """

    name = STT_MODE_BATCHED

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        if batch_size not in SUPPORTED_BATCH_SIZES:
            raise STTError(
                E_STT_FAILED,
                f"Unsupported batch_size {batch_size!r}; must be one of {SUPPORTED_BATCH_SIZES}.",
            )
        self.batch_size = batch_size

    def transcribe(self, model, audio_path, *, project_id, model_name, language, total_duration_seconds, cancel, on_progress):
        from faster_whisper.transcribe import BatchedInferencePipeline  # noqa: PLC0415

        pipeline = BatchedInferencePipeline(model)
        result = pipeline.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
            batch_size=self.batch_size,
            word_timestamps=True,
        )
        segments_raw, info = self._consume(result)
        words = _flatten_word_timestamps(segments_raw)
        resolved_language = language or getattr(info, "language", None) or "und"
        reconstructed = reconstruct_segments_from_words(words, language=resolved_language)
        if not reconstructed:
            raise STTError(E_STT_NO_SPEECH, "Batched transcription produced no speech.")
        issues = validate_segment_timestamps(reconstructed)
        if issues:
            raise STTError(
                E_STT_FAILED,
                "Batched output rejected by timestamp validation: " + "; ".join(issues),
            )
        # Reconstructed segments are dicts already in the normalized shape;
        # build_transcript consumes segment *objects*, so wrap them.
        wrapped = [SimpleNamespace(**seg) for seg in reconstructed]
        return self._build(
            wrapped,
            info,
            project_id=project_id,
            model_name=model_name,
            language=language,
            total_duration_seconds=total_duration_seconds,
            cancel=cancel,
            on_progress=on_progress,
        )


def _segment_language(segment: Any, fallback: str) -> str:
    lang = getattr(segment, "language", None)
    return lang or fallback


def _segment_confidence(segment: Any) -> float:
    # Batched reconstruction stores the average word probability directly in
    # ``confidence``; regular faster-whisper segments carry ``avg_logprob``.
    direct = getattr(segment, "confidence", None)
    if direct is not None:
        try:
            return round(max(0.0, min(1.0, float(direct))), 4)
        except (TypeError, ValueError):
            return 0.0
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
        words = getattr(segment, "words", None)
        words_out = None
        if words is not None:
            normalized = []
            for w in words:
                if isinstance(w, dict):
                    w_start = float(w.get("start", start) or start)
                    w_end = float(w.get("end", end) or end)
                    w_word = w.get("word", "") or ""
                    w_prob = w.get("probability", None)
                else:
                    w_start = float(getattr(w, "start", start) or start)
                    w_end = float(getattr(w, "end", end) or end)
                    w_word = getattr(w, "word", "") or ""
                    w_prob = getattr(w, "probability", None)
                normalized.append(
                    {
                        "word": w_word,
                        "start": round(max(start, min(end, w_start)), 3),
                        "end": round(max(start, min(end, w_end)), 3),
                        "probability": w_prob,
                    }
                )
            words_out = [w for w in normalized if w["word"]]
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
        if words_out:
            segments_out[-1]["words"] = words_out
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
    cpu_threads: int | None = None,
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
    stt_mode: str = STT_MODE_AUTO,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_stt_log: Callable[[str], None] | None = None,
) -> TranscribeResult:
    """Transcribe ``audio_path`` into a canonical Transcript.

    ``whisper_model`` is a test seam: when provided the guard/load steps are
    skipped and it is used as-is (it must expose ``transcribe(audio, ...)``
    returning ``(segments, info)`` like faster-whisper 1.x).

    ``cpu_threads`` bounds the faster-whisper worker threads per call — the
    chunked pipeline passes ``cores // stt_workers`` so concurrent chunks share
    the CPU instead of each claiming every core (see ``stt_thread_budget``).

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
        vram = available_vram_mb() if resolved_device == "cuda" else None
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
        model = _load_whisper_model(effective_model, resolved_device, resolved_compute, cpu_threads)

    mode, mode_reason = resolve_stt_mode(
        stt_mode,
        device=resolved_device,
        model_name=effective_model,
        batch_size=batch_size,
        vram_mb=vram,
    )
    logger.info("[STT] Mode resolution: %s (%s)", mode, mode_reason)
    if on_stt_log is not None:
        on_stt_log(f"[STT] Mode: {mode}")

    def _emit(msg: str) -> None:
        logger.info("%s", msg)
        if on_stt_log is not None:
            on_stt_log(msg)

    def _run_regular(mdl: Any) -> TranscribeResult:
        engine = RegularSTTEngine()
        transcript = engine.transcribe(
            mdl,
            audio_path,
            project_id=project_id,
            model_name=effective_model,
            language=language,
            total_duration_seconds=total_duration_seconds,
            cancel=cancel,
            on_progress=on_progress,
        )
        return TranscribeResult(
            transcript=transcript,
            model_used=effective_model,
            device_used=resolved_device,
            engine=STT_MODE_REGULAR,
        )

    def _run_batched(mdl: Any) -> TranscribeResult:
        engine = BatchedSTTEngine(batch_size=batch_size)
        transcript = engine.transcribe(
            mdl,
            audio_path,
            project_id=project_id,
            model_name=effective_model,
            language=language,
            total_duration_seconds=total_duration_seconds,
            cancel=cancel,
            on_progress=on_progress,
        )
        return TranscribeResult(
            transcript=transcript,
            model_used=effective_model,
            device_used=resolved_device,
            engine=STT_MODE_BATCHED,
        )

    # Choose engine with fallback. ``batched`` never hard-fails the job: any
    # failure (runtime exception, OOM, unsupported model at runtime, invalid
    # output, timestamp corruption) drops to ``regular`` and the job continues
    # safely. CUDA-lib RuntimeErrors are still retried on CPU at the end.
    transcript_result: TranscribeResult | None = None

    def _run_with_cpu_retry(build: Callable[[Any], TranscribeResult]) -> TranscribeResult:
        """Mirror the pre-existing single-pass try/except (CUDA lib -> CPU)."""
        try:
            return build(model)
        except CancelledError:
            raise
        except STTError:
            raise
        except RuntimeError as exc:  # noqa: BLE001 - see below
            if resolved_device != "cuda" or not _is_cuda_library_error(exc):
                raise STTError(E_STT_FAILED, "Transcription failed.") from exc
            logger.warning("CUDA runtime library failure (%s); retrying STT on CPU", exc)
            ensure_cuda_libraries()
            try:
                return build(_load_whisper_model(effective_model, "cpu", "int8"))
            except STTError:
                raise
            except Exception as cpu_exc:  # noqa: BLE001 - map CPU fallback failure
                raise STTError(E_STT_FAILED, "Transcription failed (CPU fallback also failed).") from cpu_exc
        except Exception as exc:  # noqa: BLE001 - map every transcription failure
            raise STTError(E_STT_FAILED, "Transcription failed.") from exc

    if mode == STT_MODE_BATCHED:
        try:
            transcript_result = _run_batched(model)
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fallback, not a hard failure
            _emit("[BATCHED_STT] Failed")
            reason = str(exc) or exc.__class__.__name__
            _emit(f"[BATCHED_STT] Reason: {reason}")
            _emit("[STT] Falling back to regular mode")
            logger.exception("Batched STT failed; falling back to regular")
            transcript_result = _run_with_cpu_retry(_run_regular)
    else:
        transcript_result = _run_with_cpu_retry(_run_regular)

    return transcript_result


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
