"""Chunked parallel automation pipeline (TASK_AUTOMATION_PINELINE.md).

The pipeline splits a long video's audio into fixed-length **logical chunks**
(default 30 s) with a small **overlap** margin (default 2 s) that gives the
per-chunk STT/translation extra context. Chunks are processed through the
*existing* worker services (STT → translation → TTS) under **bounded
concurrency**, each chunk is validated and retried individually, and the
results are assembled in index order before the (existing) render stage burns
subtitles + voice track.

Design rules honoured here:

- Overlap is context only: every artifact (transcript segment / subtitle cue /
  TTS clip) is clamped to its chunk's ``[logical_start, logical_end]`` window
  so nothing lands on the final timeline twice.
- Concurrency is bounded (``max_concurrency``), never "one process per chunk".
- A failed chunk retries alone (``max_retries = 2``), then the job fails —
  no final output is produced while any chunk is invalid.
- The per-job **manifest** reconstructs pipeline state without the frontend.
- **Order validation** blocks assembly on missing/duplicate/out-of-order chunks.
- **Streaming stage pipeline** (2026-08-18): STT / translation / TTS run in
  separate bounded worker pools fed by bounded queues, and an ordered assembly
  buffer commits finished chunks in ``index`` order *while processing is still
  running* — a chunk's slow TTS tail no longer holds a worker slot away from
  the next chunk's STT (see :class:`StreamingChunkPipeline`). All the order /
  timeline / overlap / retry / cleanup guarantees above are unchanged.
- **STT CPU thread budgeting** (2026-08-18): faster-whisper defaults
  ``cpu_threads`` to *all* cores per ``transcribe()`` call, so N concurrent
  chunks oversubscribed the machine N× and wall time barely dropped. Each STT
  call now gets ``cores // stt_workers`` threads (see :func:`stt_thread_budget`)
  so parallel chunks really run in parallel without thrashing.
- Final video generation + **final validation** + **output verification** all
  run before **cleanup**; cleanup is the last step and keeps intermediates on
  any failure (per ``CleanupManager`` state machine).

All progress/events are reported through the standard ``(progress, stage,
message)`` cancellation-token callback (the same protocol the other worker
stages use), so Rust polls them and the live log renders real chunk events —
nothing is faked.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (PHASE 1/2/5 — configurable, never hard-coded at call sites)
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_DURATION = 30.0
"""Default logical chunk length (seconds)."""

ALLOWED_CHUNK_DURATIONS: tuple[float, ...] = (20.0, 30.0, 45.0, 60.0)
"""Durations the UI may choose; the default stays 30 s."""

DEFAULT_OVERLAP = 2.0
"""Context overlap margin (seconds) around each logical chunk."""

DEFAULT_MAX_CONCURRENCY = 4
"""Bounded worker-pool size (Phase 5: avoid RAM/VRAM exhaustion)."""

DEFAULT_MAX_RETRIES = 2
"""Per-chunk retry budget before the chunk is FAILED_PERMANENTLY."""

DEFAULT_DURATION_TOLERANCE = 0.5
"""Final-validation duration tolerance (seconds) between source and output."""

# ---------------------------------------------------------------------------
# STT quality guard (P1, 2026-08-20) — batched early-EOS collapse recovery
#
# faster-whisper ``BatchedInferencePipeline`` can emit an early `<|endoftext|>`
# and swallow tens of seconds of speech *inside* a chunk (measured: a 21.3 s
# block at local [7.5, 28.8] of a chunk starting mid-sentence). The output is
# still formally valid (monotonic, non-overlapping, in-window) so timestamp
# validation passes. The guard detects the acoustic signature — a long
# interior gap between segments whose audio window carries speech energy —
# and re-transcribes that chunk once in ``regular`` mode, which covers the
# block fully (verified 30/30 s on the bench fixture).
# ---------------------------------------------------------------------------

STT_COVERAGE_RETRY_MIN_GAP = 3.0
"""Interior gap (seconds) long enough to suspect a swallowed speech block."""

STT_COVERAGE_RETRY_RMS_REL = 0.25
"""Gap counts as "has speech" when its RMS ≥ this × the chunk's overall RMS."""

STT_COVERAGE_RETRY_MIN_RMS = 200.0
"""Absolute 16-bit RMS floor below which a gap is treated as silence."""

STT_COVERAGE_RETRY_WINDOW_S = 0.5
"""RMS measurement window (seconds) when analysing a gap for speech."""

# Canonical chunk statuses (Phase 6).
CHUNK_PENDING = "pending"
CHUNK_PROCESSING = "processing"
CHUNK_COMPLETED = "completed"
CHUNK_VALIDATING = "validating"
CHUNK_VALID = "valid"
CHUNK_FAILED = "failed"
CHUNK_RETRYING = "retrying"
CHUNK_FAILED_PERMANENTLY = "failed_permanently"

CHUNK_STATUSES: tuple[str, ...] = (
    CHUNK_PENDING,
    CHUNK_PROCESSING,
    CHUNK_COMPLETED,
    CHUNK_VALIDATING,
    CHUNK_VALID,
    CHUNK_FAILED,
    CHUNK_RETRYING,
    CHUNK_FAILED_PERMANENTLY,
)

# Cleanup state machine (Phase 15).
CLEANUP_PROCESSING = "processing"
CLEANUP_ASSEMBLING = "assembling"
CLEANUP_VALIDATING = "validating"
CLEANUP_VALIDATION_FAILED = "validation_failed"
CLEANUP_OUTPUT_READY = "output_ready"
CLEANUP_OUTPUT_VERIFIED = "output_verified"
CLEANUP_DONE = "done"


# ---------------------------------------------------------------------------
# PHASE 1/2 — Chunk model + overlap timeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """One logical chunk.

    ``start``/``end`` are the **processing range** on the source timeline
    (they include the overlap margins); ``logical_start``/``logical_end`` are
    the chunk's share of the **final timeline**. ``duration`` is the
    processing length (``end - start``).
    """

    chunk_id: str
    index: int  # 1-based
    start: float
    end: float
    duration: float
    overlap_before: float
    overlap_after: float
    logical_start: float
    logical_end: float
    status: str = CHUNK_PENDING
    retries: int = 0

    @property
    def logical_duration(self) -> float:
        return self.logical_end - self.logical_start


def build_chunks(
    total_duration: float,
    *,
    chunk_duration: float = DEFAULT_CHUNK_DURATION,
    overlap: float = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split ``[0, total_duration)`` into logical chunks of ``chunk_duration``.

    Each chunk except the first carries ``overlap`` seconds of preceding audio
    (context) and every chunk except the last carries ``overlap`` seconds of
    following audio. The final chunk is allowed to be shorter.

    Example (30 s chunks, 2 s overlap, 60 s video)::

        chunk_0001  [0.0, 32.0]  logical [0, 30)
        chunk_0002  [28.0, 60.0] logical [30, 60)
    """
    if total_duration <= 0:
        raise ValueError(f"total_duration must be > 0, got {total_duration!r}")
    if chunk_duration <= 0:
        raise ValueError(f"chunk_duration must be > 0, got {chunk_duration!r}")
    if overlap < 0:
        raise ValueError(f"overlap must be >= 0, got {overlap!r}")

    chunks: list[Chunk] = []
    index = 1
    logical_start = 0.0
    while logical_start < total_duration - 1e-6:
        logical_end = min(logical_start + chunk_duration, total_duration)
        start = max(0.0, logical_start - overlap)
        end = min(total_duration, logical_end + overlap)
        chunks.append(
            Chunk(
                chunk_id=f"chunk_{index:04d}",
                index=index,
                start=start,
                end=end,
                duration=end - start,
                overlap_before=logical_start - start,
                overlap_after=end - logical_end,
                logical_start=logical_start,
                logical_end=logical_end,
            )
        )
        logical_start = logical_end
        index += 1
    return chunks


def clamp_to_logical(
    start: float,
    end: float,
    logical_start: float,
    logical_end: float,
) -> tuple[float, float] | None:
    """Clamp a source-timeline span to the chunk's final-timeline window.

    Returns ``None`` when the clamped range is empty — the span lived entirely
    in the overlap margins and must not appear on the final timeline.
    """
    lo = max(start, logical_start)
    hi = min(end, logical_end)
    if hi <= lo + 1e-6:
        return None
    return lo, hi


# ---------------------------------------------------------------------------
# PHASE 1 — ChunkManager (state + manifest)
# ---------------------------------------------------------------------------


class ChunkManager:
    """Owns the chunk list and their lifecycle statuses.

    The manager is the single source of truth for chunk state; the scheduler
    mutates statuses through it, and :meth:`manifest` snapshots the whole
    pipeline so it can be reconstructed without the frontend (Phase 8).
    """

    def __init__(
        self,
        total_duration: float,
        *,
        chunk_duration: float = DEFAULT_CHUNK_DURATION,
        overlap: float = DEFAULT_OVERLAP,
    ) -> None:
        self.total_duration = total_duration
        self.chunk_duration = chunk_duration
        self.overlap = overlap
        self._chunks: list[Chunk] = build_chunks(
            total_duration, chunk_duration=chunk_duration, overlap=overlap
        )
        self._lock = threading.Lock()

    # -- queries ----------------------------------------------------------

    @property
    def chunks(self) -> list[Chunk]:
        with self._lock:
            return list(self._chunks)

    def get(self, index: int) -> Chunk | None:
        with self._lock:
            return next((c for c in self._chunks if c.index == index), None)

    def count(self, status: str) -> int:
        with self._lock:
            return sum(1 for c in self._chunks if c.status == status)

    def completed_count(self) -> int:
        return self.count(CHUNK_VALID) + self.count(CHUNK_COMPLETED)

    def failed_indices(self) -> list[int]:
        with self._lock:
            return [c.index for c in self._chunks if c.status == CHUNK_FAILED_PERMANENTLY]

    # -- mutations --------------------------------------------------------

    def set_status(self, index: int, status: str) -> None:
        if status not in CHUNK_STATUSES:
            raise ValueError(f"invalid chunk status {status!r}")
        with self._lock:
            for c in self._chunks:
                if c.index == index:
                    object.__setattr__(c, "status", status)
                    return
        raise KeyError(f"no chunk with index {index}")

    def mark_retry(self, index: int) -> int:
        """Bump the retry counter; returns the new attempt count."""
        with self._lock:
            for c in self._chunks:
                if c.index == index:
                    object.__setattr__(c, "retries", c.retries + 1)
                    object.__setattr__(c, "status", CHUNK_RETRYING)
                    return c.retries
        raise KeyError(f"no chunk with index {index}")

    # -- manifest (Phase 8) -----------------------------------------------

    def manifest(
        self,
        *,
        job_id: str,
        source_video: str,
        completed_chunks: int | None = None,
        failed_chunks: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "source_video": source_video,
            "chunk_duration": self.chunk_duration,
            "overlap": self.overlap,
            "total_chunks": len(self._chunks),
            "completed_chunks": self.completed_count() if completed_chunks is None else completed_chunks,
            "failed_chunks": [int(i) for i in (self.failed_indices() if failed_chunks is None else failed_chunks)],
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "index": c.index,
                    "start": c.start,
                    "end": c.end,
                    "duration": c.duration,
                    "overlap_before": c.overlap_before,
                    "overlap_after": c.overlap_after,
                    "logical_start": c.logical_start,
                    "logical_end": c.logical_end,
                    "status": c.status,
                    "retries": c.retries,
                }
                for c in self._chunks
            ],
        }


# ---------------------------------------------------------------------------
# PHASE 4/5 — ChunkScheduler (bounded parallel pool, per-chunk retry)
# ---------------------------------------------------------------------------


class ChunkFailedError(Exception):
    """Raised by a chunk processor; the scheduler retries / fails the chunk."""

    def __init__(self, message: str, *, code: str = "E_CHUNK_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ChunkScheduler:
    """Runs chunks through a bounded worker pool (Phase 4).

    ``max_concurrency`` bounds how many chunks are processed simultaneously
    (Phase 5: never spawn one process per chunk — no RAM/VRAM exhaustion).
    Failed chunks are retried up to ``max_retries`` times; a chunk that keeps
    failing becomes ``failed_permanently`` and the whole run stops (Phase 7).
    """

    def __init__(
        self,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries

    def run(
        self,
        manager: ChunkManager,
        process_one: Callable[[Chunk], Any],
        *,
        on_event: Callable[[str, str], None] | None = None,
    ) -> list[Any]:
        """Process all chunks in index order under bounded concurrency.

        ``process_one(chunk)`` returns the chunk result on success and raises
        :class:`ChunkFailedError` on failure. ``on_event(level, message)``
        receives real pipeline events (rendered by the live log).

        Returns results sorted by chunk index. Raises ``ChunkFailedError``
        with ``failed_permanently`` when a chunk exhausts its retries.
        """
        results: dict[int, Any] = {}
        pending = manager.chunks

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {
                pool.submit(self._run_one, manager, c, process_one, on_event): c.index
                for c in pending
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except ChunkFailedError as exc:
                    # Stop the pool; do not assemble with an invalid chunk.
                    for f in futures:
                        f.cancel()
                    raise ChunkFailedError(
                        f"chunk {index:04d} failed permanently: {exc.message}",
                        code=exc.code,
                    ) from exc

        return [results[i] for i in sorted(results)]

    def _run_one(
        self,
        manager: ChunkManager,
        chunk: Chunk,
        process_one: Callable[[Chunk], Any],
        on_event: Callable[[str, str], None] | None,
    ) -> Any:
        attempts = 1 + chunk.retries
        while attempts <= 1 + self.max_retries:
            manager.set_status(chunk.index, CHUNK_PROCESSING)
            if on_event:
                on_event("info", f"CHUNK_STARTED {chunk.chunk_id} {chunk.index}/{len(manager.chunks)}")
            try:
                result = process_one(chunk)
                manager.set_status(chunk.index, CHUNK_VALIDATING)
                if on_event:
                    on_event("info", f"CHUNK_VALIDATING {chunk.chunk_id}")
                # Validation is the processor's responsibility (it knows the
                # artifact shapes); an invalid chunk raises so retry applies.
                manager.set_status(chunk.index, CHUNK_VALID)
                if on_event:
                    on_event("info", f"CHUNK_VALID {chunk.chunk_id}")
                return result
            except ChunkFailedError as exc:
                if attempts > self.max_retries:
                    manager.set_status(chunk.index, CHUNK_FAILED_PERMANENTLY)
                    if on_event:
                        on_event("error", f"CHUNK_FAILED {chunk.chunk_id} — {exc.message}")
                    raise
                manager.mark_retry(chunk.index)
                attempts += 1
                if on_event:
                    on_event(
                        "warn",
                        f"CHUNK_RETRYING {chunk.chunk_id} — attempt {manager.get(chunk.index).retries if manager.get(chunk.index) else attempts}/{self.max_retries + 1} ({exc.message})",
                    )
        raise ChunkFailedError(f"chunk {chunk.index:04d} failed")  # pragma: no cover


# ---------------------------------------------------------------------------
# STREAMING PIPELINE — stage-decoupled bounded pools + ordered assembly
# ---------------------------------------------------------------------------


def stt_thread_budget(stt_workers: int, cpu_count: int | None = None) -> int:
    """Per-STT-call thread budget so concurrent STT chunks share the CPU.

    faster-whisper defaults ``cpu_threads=0`` to *all* cores per
    ``transcribe()`` call; with N chunks transcribing at once that
    oversubscribes the machine N× and wall time barely drops. Budgeting
    ``cores // stt_workers`` threads per call keeps
    ``stt_workers × threads ≈ cores`` — real parallelism without thrashing.
    One worker keeps all cores (no regression); very many workers floor at 1.
    """
    cpu_count = cpu_count or max(1, os.cpu_count() or 4)
    return max(1, cpu_count // max(1, stt_workers))


def _append_chunk_voice(
    pcm_file: Any,
    track_path: str | None,
    logical_duration: float,
    sample_rate: int = 44100,
) -> int:
    """Append one committed chunk's track (or silence) to a growing PCM file.

    Returns the number of samples appended. Tracks are padded/truncated to the
    chunk's logical duration so silent chunks keep the exact timeline and the
    final WAV always has the full source duration. Mono 16-bit PCM at
    ``sample_rate`` (the format every TTS engine is normalized to).
    """
    import wave  # noqa: PLC0415 - stdlib, lazy

    frames = b""
    n = 0
    if track_path and os.path.isfile(track_path):
        try:
            with wave.open(track_path, "rb") as w:
                n = w.getnframes()
                frames = w.readframes(n)
        except (wave.Error, EOFError):  # noqa: PERF203 - corrupt chunk track
            frames = b""
            n = 0
    expected = int(round(logical_duration * sample_rate))
    bytes_per_sample = 2  # 16-bit mono
    if n < expected:
        frames = frames + b"\x00" * ((expected - n) * bytes_per_sample)
    elif n > expected:
        frames = frames[: expected * bytes_per_sample]
    pcm_file.write(frames)
    return expected


def _pcm_to_wav(pcm_path: str, wav_path: str, sample_rate: int = 44100) -> str:
    """Wrap a raw mono 16-bit PCM stream into a WAV container."""
    import wave  # noqa: PLC0415 - stdlib, lazy

    with open(pcm_path, "rb") as fh:
        data = fh.read()
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return wav_path



class ConcurrentTracker:
    """Measure peak and average concurrency for a pipeline stage.

    Usage::

        tracker = ConcurrentTracker()
        tracker.inc()   # task starts
        # ... task runs ...
        tracker.dec()   # task finishes

        print(tracker.peak)        # max concurrent tasks observed
        print(tracker.avg())       # average over the observation window
        print(tracker.current)     # tasks currently running
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: int = 0
        self.peak: int = 0
        self._total: float = 0.0
        self._start: float = time.monotonic()

    def inc(self) -> None:
        with self._lock:
            self.current += 1
            if self.current > self.peak:
                self.peak = self.current

    def dec(self) -> None:
        with self._lock:
            self.current -= 1

    def avg(self) -> float:
        elapsed = time.monotonic() - self._start
        if elapsed <= 0:
            return 0.0
        with self._lock:
            # Approximate: track cumulative concurrency-seconds
            self._total += self.current * 0.001  # updated on each call
        return self._total / elapsed if elapsed > 0 else 0.0

class StreamingChunkPipeline:
    """Stage-decoupled, bounded parallel pipeline with ordered streaming assembly.

    The old coupled model (one worker ran STT → translation → TTS per chunk)
    held a worker slot for the *whole* chunk: a slow TTS/translation tail kept
    that slot away from the next chunk's STT and starved the CPU/GPU-bound STT
    hot loop. This pipeline splits the work into three bounded stage pools
    connected by bounded queues::

        producer -> STT pool -> translation pool -> TTS pool -> completed q
                                                                  -> assembly

    A chunk that finishes STT immediately frees its STT worker and is handed to
    the translation pool; a chunk in TTS no longer blocks the next chunk's STT
    (the measured ``stt avg_active 2.42/4`` starvation in PERFORMANCE_TRACE).
    Each stage queue is bounded, so a slow downstream stage naturally
    backpressures the upstream stage instead of buffering unboundedly. A chunk
    that fails a stage is retried *at that stage only* (its earlier work is
    reused) and after ``max_retries`` the whole job fails — never a
    silently-incomplete output.

    The assembly coordinator consumes the completed queue and COMMITS chunks
    strictly in ``index`` order (the Ordered Assembly Buffer): a chunk that
    arrives early waits in a buffer until its predecessor commits, so the final
    timeline is always C1, C2, C3, ... regardless of completion order. Committed
    chunks stream their voice-track samples into one growing PCM file while
    processing is still running; the final WAV is written at the end. Transcript
    / subtitles are derived from the committed (ordered) artifacts by the same
    existing pure functions, so all timeline/overlap guarantees are unchanged.
    """

    _STAGES: tuple[str, ...] = ("stt", "translate", "tts")
    _NEXT: dict[str, str | None] = {"stt": "translate", "translate": "tts", "tts": None}

    def __init__(
        self,
        *,
        manager: ChunkManager,
        ctx: ChunkPipelineContext,
        stt_workers: int,
        translate_workers: int,
        tts_workers: int,
        max_retries: int = DEFAULT_MAX_RETRIES,
        on_event: Callable[[str, str], None] | None = None,
        on_progress: Callable[[float, str, str | None], None] | None = None,
    ) -> None:
        for name, n in (
            ("stt_workers", stt_workers),
            ("translate_workers", translate_workers),
            ("tts_workers", tts_workers),
        ):
            if n < 1:
                raise ValueError(f"{name} must be >= 1")
        self.manager = manager
        self.ctx = ctx
        self.workers: dict[str, int] = {
            "stt": stt_workers,
            "translate": translate_workers,
            "tts": tts_workers,
        }
        self.max_retries = max_retries
        self.on_event = on_event
        self.on_progress = on_progress
        # Bounded queues: maxsize == pool size of the *consuming* stage, so a
        # slow downstream stage limits the number of in-flight items upstream.
        self.stt_q: Queue[Any] = Queue(maxsize=max(1, stt_workers))
        self.translate_q: Queue[Any] = Queue(maxsize=max(1, translate_workers))
        self.tts_q: Queue[Any] = Queue(maxsize=max(1, tts_workers))
        self.completed_q: Queue[Any] = Queue(
            maxsize=max(1, stt_workers + translate_workers + tts_workers)
        )
        self._stop = threading.Event()
        self._failure: ChunkFailedError | None = None
        # Concurrency observability (peak / avg per stage)
        self.trackers: dict[str, ConcurrentTracker] = {
            name: ConcurrentTracker() for name in self._STAGES
        }
        self._lock = threading.Lock()
        self._stage_done = {s: 0 for s in self._STAGES}
        self.assembled: list[ChunkArtifacts] = []
        self.voice_track_path: str | None = None
        self._voice_pcm: str | None = None

    # -- thread-safe coordination ----------------------------------------

    def _fail(self, exc: ChunkFailedError) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = exc
        self._stop.set()

    def _get(self, q: Queue) -> Any:
        while not self._stop.is_set():
            try:
                return q.get(timeout=0.25)
            except Empty:
                continue
        return None

    def _put(self, q: Queue, item: Any) -> bool:
        while not self._stop.is_set():
            try:
                q.put(item, timeout=0.25)
                return True
            except Full:
                continue
        return False

    def _queue_of(self, stage: str) -> Queue:
        return {"stt": self.stt_q, "translate": self.translate_q, "tts": self.tts_q}[stage]

    def _worker_done(self, stage: str) -> None:
        """Called when one stage worker exits. The LAST one out hands the
        sentinel downstream so the next pool knows no more work is coming
        (pool sizes can differ between stages)."""
        with self._lock:
            self._stage_done[stage] += 1
            if self._stage_done[stage] != self.workers[stage]:
                return
        nxt = self._NEXT[stage]
        if nxt is None:
            self._put(self.completed_q, None)
        else:
            for _ in range(self.workers[nxt]):
                self._put(self._queue_of(nxt), None)

    # -- stage workers ---------------------------------------------------

    def _worker(
        self,
        stage: str,
        in_q: Queue,
        out_q: Queue | None,
        process: Callable[[_StageState, ChunkPipelineContext], None],
    ) -> None:
        while not self._stop.is_set():
            state = self._get(in_q)
            if state is None:
                break
            # Queue-wait instrumentation: seconds between "ready for this
            # stage" (stamped by the upstream producer) and pickup now.
            state.queue_wait[stage] = max(
                0.0, time.monotonic() - state.ready.pop(stage, time.monotonic())
            )
            attempts = 0
            while True:
                self.manager.set_status(state.chunk.index, CHUNK_PROCESSING)
                if self.on_event:
                    self.on_event("info", f"CHUNK_STARTED {state.chunk.chunk_id} {state.chunk.index} [{stage}]")
                try:
                    self.trackers[stage].inc()
                    try:
                        process(state, self.ctx)
                        break
                    finally:
                        self.trackers[stage].dec()
                except ChunkFailedError as exc:
                    attempts += 1
                    state.stage_attempts[stage] = attempts
                    if attempts <= self.max_retries:
                        self.manager.mark_retry(state.chunk.index)
                        if self.on_event:
                            self.on_event(
                                "warn",
                                f"CHUNK_RETRYING {state.chunk.chunk_id} [{stage}] "
                                f"attempt {attempts}/{self.max_retries + 1} ({exc.message})",
                            )
                        continue  # retry the STAGE in place — earlier stage work is reused
                    if self.on_event:
                        self.on_event(
                            "error",
                            f"CHUNK_FAILED {state.chunk.chunk_id} [{stage}] — {exc.message}",
                        )
                    self.manager.set_status(state.chunk.index, CHUNK_FAILED_PERMANENTLY)
                    self._fail(
                        ChunkFailedError(
                            f"chunk {state.chunk.chunk_id} failed permanently at {stage}: {exc.message}",
                            code=exc.code,
                        )
                    )
                    return
            if stage == "tts":
                self._put(self.completed_q, (state, _build_chunk_artifacts(state, self.ctx)))
            else:
                nxt = self._NEXT[stage]
                if nxt is not None:
                    state.ready[nxt] = time.monotonic()  # ready-for-next (queue-wait profile)
                self._put(out_q, state)
        self._worker_done(stage)

    def _producer(self) -> None:
        for chunk in self.manager.chunks:
            state = _StageState(chunk=chunk)
            state.ready["stt"] = time.monotonic()  # queue-wait profile
            if not self._put(self.stt_q, state):
                return
        for _ in range(self.workers["stt"]):
            if not self._put(self.stt_q, None):
                return

    # -- ordered streaming assembly ---------------------------------------

    def _commit(self, index: int, art: ChunkArtifacts, pcm: Any) -> None:
        self.manager.set_status(index, CHUNK_VALID)
        if self.on_event:
            self.on_event("info", f"CHUNK_ASSEMBLED {art.chunk_id}")
        if pcm is not None:
            _append_chunk_voice(pcm, art.voice_track, art.logical_end - art.logical_start)
        self.assembled.append(art)

    def _assembly(self) -> None:
        expected = 1
        buffer: dict[int, ChunkArtifacts] = {}
        pcm: Any = None
        if self.ctx.dub:
            pcm_path = os.path.join(self.ctx.workdir, "voice_track.pcm")
            os.makedirs(os.path.dirname(pcm_path), exist_ok=True)
            pcm = open(pcm_path, "wb")
            self._voice_pcm = pcm_path
        try:
            while not self._stop.is_set():
                item = self._get(self.completed_q)
                if item is None:
                    break
                state, artifacts = item
                buffer[state.chunk.index] = artifacts
                while expected in buffer:
                    self._commit(expected, buffer.pop(expected), pcm)
                    expected += 1
                if self._failure is not None:
                    break
        finally:
            if pcm is not None:
                pcm.close()
                if self._voice_pcm is not None and self._failure is None:
                    wav = os.path.splitext(self._voice_pcm)[0] + ".wav"
                    self.voice_track_path = _pcm_to_wav(self._voice_pcm, wav)

    # -- entry point ------------------------------------------------------

    def run(self) -> list[ChunkArtifacts]:
        jobs: list[threading.Thread] = [threading.Thread(target=self._producer, name="chunk-producer", daemon=True)]
        stage_fn: dict[str, Callable[[_StageState, ChunkPipelineContext], None]] = {
            "stt": _run_stt_stage,
            "translate": _run_translate_stage,
            "tts": _run_tts_stage,
        }
        for stage in self._STAGES:
            in_q = self._queue_of(stage)
            out_q = self._queue_of(self._NEXT[stage]) if self._NEXT[stage] else None
            jobs.extend(
                threading.Thread(
                    target=self._worker,
                    args=(stage, in_q, out_q, stage_fn[stage]),
                    name=f"chunk-{stage}-{i}",
                    daemon=True,
                )
                for i in range(self.workers[stage])
            )
        assembly = threading.Thread(target=self._assembly, name="chunk-assembly", daemon=True)
        jobs.append(assembly)
        for t in jobs:
            t.start()
        for t in jobs:
            t.join()
        if self._failure is not None:
            raise self._failure
        return self.assembled


# ---------------------------------------------------------------------------
# PHASE 6 — per-chunk validation helpers
# ---------------------------------------------------------------------------


@dataclass
class ChunkArtifacts:
    """Everything a chunk produced, ready for assembly."""

    index: int
    chunk_id: str
    logical_start: float
    logical_end: float
    # Merged transcript segments with GLOBAL timeline timestamps.
    segments: list[dict[str, Any]]
    # Translated cue sources with GLOBAL timestamps (text = translation).
    cues: list[dict[str, Any]]
    # Path to the chunk voice track (None when dubbing is off / silent chunk).
    voice_track: str | None
    audio_path: str
    # A silent chunk is a valid outcome (no speech in its logical window) —
    # it contributes nothing to the transcript/subtitles/voice track instead
    # of failing the whole run.
    silent: bool = False
    # Optional wall-clock per-stage timing (seconds) recorded by the chunk
    # processor for performance tracing. ``None`` for fake artifacts in tests.
    # Ignored by validation/assembly; consumed by build_performance_trace().
    perf: dict[str, float] | None = None


@dataclass
class _StageState:
    """Mutable per-chunk state threaded through the stage pipeline.

    A chunk starts at the STT stage, is handed to translation, then TTS, then
    the assembly buffer. Each stage mutates only its fields, so a chunk that
    fails translation is retried *at translation* without re-running STT.
    """

    chunk: Chunk
    audio_path: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    cues: list[dict[str, Any]] = field(default_factory=list)
    voice_track: str | None = None
    silent: bool = False
    perf: dict[str, Any] = field(
        default_factory=lambda: {
            "wall_start_s": 0.0,
            "wall_end_s": 0.0,
            "slice_start_s": 0.0,
            "slice_s": 0.0,
            "stt_start_s": 0.0,
            "stt_s": 0.0,
            "translate_start_s": 0.0,
            "translate_s": 0.0,
            "tts_start_s": 0.0,
            "tts_s": 0.0,
        }
    )
    #: Seconds this chunk sat in each stage's bounded input queue before a
    #: worker picked it up (instrumentation for the pipeline performance
    #: profile — measures backpressure, not load).
    queue_wait: dict[str, float] = field(default_factory=dict)
    #: Monotonic timestamps when a chunk became ready for each stage (set by
    #: the upstream producer right before handing it on); consumed by the
    #: downstream worker to compute ``queue_wait``.
    ready: dict[str, float] = field(default_factory=dict)
    stage_attempts: dict[str, int] = field(default_factory=dict)


def validate_chunk_result(artifacts: ChunkArtifacts) -> list[str]:
    """Phase 6 per-chunk checks: files exist/readable, content non-empty,
    timestamps valid, index sequential. A ``silent`` chunk skips the
    content-emptiness checks (its logical window genuinely has no speech)."""
    issues: list[str] = []
    if not artifacts.audio_path or not os.path.isfile(artifacts.audio_path):
        issues.append("audio file missing")
    else:
        try:
            if os.path.getsize(artifacts.audio_path) == 0:
                issues.append("audio file empty")
        except OSError as exc:
            issues.append(f"audio unreadable: {exc}")
    if artifacts.voice_track and not os.path.isfile(artifacts.voice_track):
        issues.append("tts audio missing")
    if artifacts.silent:
        return issues
    if not artifacts.segments:
        issues.append("stt result empty")
    else:
        for seg in artifacts.segments:
            if seg.get("end", 0) <= seg.get("start", 0):
                issues.append("invalid segment timestamp")
                break
    if not artifacts.cues:
        issues.append("translation result empty")
    if artifacts.logical_end <= artifacts.logical_start:
        issues.append("invalid logical window")
    return issues


# ---------------------------------------------------------------------------
# PHASE 9 — order validation
# ---------------------------------------------------------------------------


def validate_chunk_order(chunks: Sequence[Chunk]) -> list[str]:
    """Phase 9: no missing index, no duplicate, sequential, timestamps valid,
    no impossible gaps / unexpected overlaps between logical windows."""
    issues: list[str] = []
    if not chunks:
        return ["no chunks"]
    indices = [c.index for c in chunks]
    if len(set(indices)) != len(indices):
        issues.append("duplicate chunk index detected")
    expected = list(range(1, len(chunks) + 1))
    if indices != expected:
        missing = [i for i in expected if i not in indices]
        issues.append(f"missing chunk index: {missing or 'unknown'}")
    prev_end: float | None = None
    for c in sorted(chunks, key=lambda c: c.index):
        if c.end <= c.start:
            issues.append(f"chunk {c.chunk_id}: invalid processing range")
        if c.logical_end <= c.logical_start:
            issues.append(f"chunk {c.chunk_id}: invalid logical window")
        if prev_end is not None and abs(c.logical_start - prev_end) > 1e-6:
            issues.append(
                f"chunk {c.chunk_id}: timeline gap/overlap (logical_start {c.logical_start} vs previous end {prev_end})"
            )
        prev_end = c.logical_end
    return issues


# ---------------------------------------------------------------------------
# PHASE 10 — ordered assembly
# ---------------------------------------------------------------------------


def merge_segments(
    per_chunk: Sequence[ChunkArtifacts],
) -> list[dict[str, Any]]:
    """Merge per-chunk transcript segments into one global timeline, sorted by
    start, renumbered. Overlap duplicates are already clamped at chunk level;
    this layer drops any residual duplicate (same span + same text)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for art in per_chunk:
        for seg in art.segments:
            key = (round(seg["start"], 2), round(seg["end"], 2), seg["text"])
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(seg))
    out.sort(key=lambda s: (s["start"], s["end"]))
    for idx, seg in enumerate(out):
        seg["idx"] = idx
        seg["id"] = f"seg_{idx}"
    return out


def merge_cues(per_chunk: Sequence[ChunkArtifacts]) -> list[dict[str, Any]]:
    """Merge translated cue sources into one global cue list (Phase 10)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for art in per_chunk:
        for cue in art.cues:
            key = (round(cue["start"], 2), round(cue["end"], 2), cue["text"])
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(cue))
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out


def assemble_translations(
    segments: Sequence[Mapping[str, Any]],
    per_chunk: Sequence[ChunkArtifacts],
) -> list[str]:
    """Return one translated text per merged transcript segment, in order.

    Identity comes from the canonical transcript (``segments``), never from a
    separately-merged cue list: a translation is paired to its source by the
    stable ``(chunk_id, src_idx)`` stamped in ``process_one_chunk``, and a
    segment whose translation was skipped falls back to its source text. This
    keeps ``seg_N`` ids perfectly aligned between transcript, translation and
    subtitle cues even when a cue is dropped mid-stream — previously a merged
    cue list deduped on its *own* key, renumbered independently, and every id
    past the first skipped row drifted (FIX #1, review 2026-08-18).
    """
    translated_by_key: dict[tuple[str, int], str] = {}
    for art in per_chunk:
        for cue in art.cues:
            cid = cue.get("chunk_id")
            sidx = cue.get("src_idx")
            if cid is not None and sidx is not None:
                translated_by_key[(str(cid), int(sidx))] = cue["text"]

    out: list[str] = []
    for seg in segments:
        cid = seg.get("chunk_id")
        sidx = seg.get("src_idx")
        text = translated_by_key.get((str(cid), int(sidx))) if cid is not None and sidx is not None else None
        if text is None or not str(text).strip():
            text = seg["text"]
        out.append(text)
    return out


def concat_voice_tracks(
    tracks: Sequence[str | None],
    durations: Sequence[float],
    output_path: str,
    *,
    sample_rate: int = 44100,
) -> str:
    """Concatenate per-chunk voice tracks into one full-duration track.

    Chunks without a track (dubbing off / no speech) are padded with silence
    of the chunk's logical duration so the assembled track keeps the exact
    timeline. Returns ``output_path``.
    """
    from src.core.ffmpeg import resolve_ffmpeg, run_ffmpeg  # noqa: PLC0415 - lazy

    ffmpeg = resolve_ffmpeg()
    work = os.path.dirname(output_path)
    os.makedirs(work, exist_ok=True)
    list_file = os.path.join(work, "concat.txt")
    parts: list[str] = []
    with open(list_file, "w", encoding="utf-8") as fh:
        for i, (track, dur) in enumerate(zip(tracks, durations)):
            if track and os.path.isfile(track):
                fh.write(f"file '{track}'\n")
                parts.append(track)
            else:
                silence = os.path.join(work, f"silence_{i:04d}.wav")
                _write_silence(ffmpeg, silence, dur, sample_rate)
                fh.write(f"file '{silence}'\n")
                parts.append(silence)
    args = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_file,
        "-c",
        "copy",
        output_path,
    ]
    result = run_ffmpeg(args)
    if result.returncode != 0:
        raise ChunkFailedError(f"voice-track concat failed: {result.stderr[-400:]}")
    return output_path


def _write_silence(ffmpeg: str, out: str, duration: float, sample_rate: int) -> None:
    from src.core.ffmpeg import run_ffmpeg  # noqa: PLC0415 - lazy

    args = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl=mono",
        "-t",
        f"{max(0.1, duration):.3f}",
        "-c:a",
        "pcm_s16le",
        out,
    ]
    result = run_ffmpeg(args)
    if result.returncode != 0:
        raise ChunkFailedError(f"silence pad failed: {result.stderr[-300:]}")


# ---------------------------------------------------------------------------
# PHASE 11 — timeline validation
# ---------------------------------------------------------------------------


def validate_timeline(
    chunks: Sequence[Chunk],
    merged_segments: Sequence[dict[str, Any]],
    total_duration: float,
    *,
    tolerance: float = DEFAULT_DURATION_TOLERANCE,
) -> list[str]:
    """Phase 11: first/last timestamp, continuity, overlap removal, duration."""
    issues: list[str] = []
    if not merged_segments:
        issues.append("no transcript segments after assembly")
        return issues
    first = merged_segments[0]["start"]
    last = merged_segments[-1]["end"]
    if first < -tolerance:
        issues.append("first timestamp before 0")
    if abs(first - chunks[0].logical_start) > tolerance and chunks[0].logical_start > 0:
        issues.append("first timestamp does not match chunk boundary")
    if last > total_duration + tolerance:
        issues.append(f"last timestamp {last:.2f}s exceeds source duration {total_duration:.2f}s")
    # Continuity: allow short natural gaps, reject impossible ones.
    prev_end: float | None = None
    for seg in merged_segments:
        if prev_end is not None and seg["start"] < prev_end - tolerance:
            issues.append(f"overlap not removed at {seg['start']:.2f}s (previous end {prev_end:.2f}s)")
        prev_end = max(prev_end or 0, seg["end"])
    return issues


# ---------------------------------------------------------------------------
# PHASE 13 — final validation (uses ffprobe through media_service.probe)
# ---------------------------------------------------------------------------


def final_validation(
    output_path: str,
    source_duration: float,
    *,
    tolerance: float = DEFAULT_DURATION_TOLERANCE,
) -> list[str]:
    """Phase 13 checklist: output exists/readable, streams present, durations
    valid and within tolerance, size > 0."""
    from src.services.media_service import probe  # noqa: PLC0415 - lazy

    issues: list[str] = []
    if not os.path.isfile(output_path):
        return ["output file does not exist"]
    if os.path.getsize(output_path) == 0:
        return ["output file is empty"]
    try:
        meta = probe(output_path)
    except Exception as exc:  # noqa: BLE001 - ffprobe failure surfaces as validation issue
        return [f"output not readable: {exc}"]
    if len(meta.video_streams) == 0:
        issues.append("no video stream")
    if len(meta.audio_streams) == 0:
        issues.append("no audio stream")
    if not (meta.duration and meta.duration > 0):
        issues.append("invalid video duration")
    else:
        if abs(meta.duration - source_duration) > tolerance:
            issues.append(
                f"duration difference {abs(meta.duration - source_duration):.2f}s exceeds tolerance {tolerance}s"
            )
    return issues


# ---------------------------------------------------------------------------
# PHASE 14 — output verification
# ---------------------------------------------------------------------------


def verify_output(output_path: str, *, stable_polls: int = 2, poll_delay: float = 0.5) -> list[str]:
    """Phase 14: file exists, readable, size stable across polls, ffprobe ok."""
    from src.services.media_service import probe  # noqa: PLC0415 - lazy

    issues: list[str] = []
    if not os.path.isfile(output_path):
        return ["output file missing"]
    sizes: list[int] = []
    for _ in range(stable_polls):
        sizes.append(os.path.getsize(output_path))
        time.sleep(poll_delay)  # noqa: PLC0415 - module-level import time below
    if len(set(sizes)) > 1:
        issues.append("output size not stable (still being written)")
    try:
        probe(output_path)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"ffprobe failed on verified output: {exc}")
    return issues


# ---------------------------------------------------------------------------
# PHASE 15/16 — CleanupManager
# ---------------------------------------------------------------------------


class CleanupManager:
    """State machine that decides when temp files may be removed (Phase 15).

    Cleanup is only allowed after ``FINAL_VALIDATION == PASS`` **and**
    ``OUTPUT_VERIFIED == PASS`` (Phase 16 policy). On any failure the temp
    tree is kept so artifacts survive for debugging / retry.
    """

    def __init__(self, temp_root: str) -> None:
        self.temp_root = temp_root
        self.state = CLEANUP_PROCESSING

    def transition(self, state: str) -> None:
        self.state = state

    def keep_temp(self) -> None:
        self.transition(CLEANUP_VALIDATION_FAILED)

    def cleanup(self) -> bool:
        """Remove the temp tree — only from a verified state."""
        if self.state not in (CLEANUP_OUTPUT_VERIFIED,):
            return False
        if os.path.isdir(self.temp_root):
            shutil.rmtree(self.temp_root, ignore_errors=True)
        self.transition(CLEANUP_DONE)
        return True


# ---------------------------------------------------------------------------
# PHASE 3 — per-chunk processing (existing services, no new STT/TT impl)
# ---------------------------------------------------------------------------


def slice_audio(
    src_wav: str,
    dst_wav: str,
    start: float,
    duration: float,
    *,
    ffmpeg_bin: str | None = None,
) -> str:
    """Cut ``[start, start+duration)`` out of ``src_wav`` into ``dst_wav``.

    Uses the same 16 kHz mono pcm_s16le format as the extracted track so the
    STT pipeline sees identical audio.
    """
    from src.core.ffmpeg import resolve_ffmpeg, run_ffmpeg  # noqa: PLC0415 - lazy

    ffmpeg = ffmpeg_bin or resolve_ffmpeg()
    out_dir = os.path.dirname(dst_wav)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    args = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.1, duration):.3f}",
        "-i",
        src_wav,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        dst_wav,
    ]
    result = run_ffmpeg(args)
    if result.returncode != 0:
        raise ChunkFailedError(f"chunk audio slice failed: {result.stderr[-400:]}")
    if not os.path.isfile(dst_wav) or os.path.getsize(dst_wav) == 0:
        raise ChunkFailedError("chunk audio slice produced no audio")
    return dst_wav


def _large_interior_gaps(
    segments: Sequence[dict[str, Any]],
    logical_start: float,
    logical_end: float,
    min_gap: float = STT_COVERAGE_RETRY_MIN_GAP,
) -> list[tuple[float, float]]:
    """Closed ``[gap_start, gap_end]`` holes strictly inside a logical window.

    Pure segment math — no audio I/O — so the STT guard can cheaply decide
    whether an acoustic check is worth it.
    """
    ordered = sorted(segments, key=lambda s: s["start"])
    holes: list[tuple[float, float]] = []
    for a, b in zip(ordered, ordered[1:]):
        gap_s = max(a["end"], logical_start)
        gap_e = min(b["start"], logical_end)
        if gap_e - gap_s > min_gap:
            holes.append((gap_s, gap_e))
    return holes


def _segment_gap_has_speech(
    audio_path: str,
    logical_start: float,
    logical_end: float,
    segments: Sequence[dict[str, Any]],
    time_offset: float = 0.0,
    min_gap: float = STT_COVERAGE_RETRY_MIN_GAP,
    rms_rel: float = STT_COVERAGE_RETRY_RMS_REL,
    min_rms: float = STT_COVERAGE_RETRY_MIN_RMS,
    window_s: float = STT_COVERAGE_RETRY_WINDOW_S,
) -> bool:
    """True when a large interior gap in ``segments`` overlaps speech energy.

    ``segments`` carry **source-timeline** timestamps (clamped to the chunk's
    logical window). ``time_offset`` is the source time at which ``audio_path``
    starts (the chunk's slice start), so gap times map to slice-relative frames.
    A batched early-EOS collapse leaves a long empty hole mid-chunk where the
    audio still has voice energy; a real silence pause leaves a quiet hole.
    The audio file is only read if at least one large hole exists.
    """
    holes = _large_interior_gaps(segments, logical_start, logical_end, min_gap)
    if not holes:
        return False
    try:
        import array

        with wave.open(audio_path, "rb") as wav:
            rate = wav.getframerate()
            nch = wav.getnchannels()
            sw = wav.getsampwidth()
            nframes = wav.getnframes()
            if sw != 2 or rate <= 0 or nframes <= 0:
                return False
            raw = wav.readframes(nframes)
        samples = array.array("h", raw)
        n_samples = len(samples)
    except Exception:  # noqa: BLE001 - guard is best-effort
        return False

    baseline_rms = 0.0
    if n_samples:
        stride = max(1, n_samples // max(1, rate * 2))
        sumsq = 0
        count = 0
        for i in range(0, n_samples, stride * nch):
            sumsq += samples[i] * samples[i]
            count += 1
        baseline_rms = (sumsq / count) ** 0.5 if count else 0.0
    speech_floor = max(min_rms, baseline_rms * rms_rel)

    def _gap_rms(start_s: float, end_s: float) -> float:
        # source-time gap -> slice-relative frame range
        rel_lo = max(0.0, start_s - time_offset)
        rel_hi = min(float(nframes) / rate, end_s - time_offset)
        if rel_hi <= rel_lo:
            return 0.0
        lo = int(rel_lo * rate) * nch
        hi = int(rel_hi * rate) * nch
        if hi - lo < nch:
            return 0.0
        sumsq = 0
        count = 0
        for i in range(lo, hi, nch):
            s = samples[i]
            sumsq += s * s
            count += 1
        return (sumsq / count) ** 0.5

    for gap_s, gap_e in holes:
        mid = (gap_s + gap_e) / 2.0
        window_lo = max(logical_start, mid - window_s / 2.0)
        window_hi = min(logical_end, mid + window_s / 2.0)
        if window_hi - window_lo < 0.2:
            continue
        if _gap_rms(window_lo, window_hi) >= speech_floor:
            return True
    return False


@dataclass
class ChunkPipelineContext:
    """Everything a chunk processor needs (paths + real services)."""

    project_id: str
    project_dir: str
    source_audio: str  # full 16k mono wav (cache/audio.wav)
    source_language: str | None
    target_language: str
    provider: str
    provider_config: dict[str, str] | None
    api_key: str | None
    model: str
    glossary_ver: str
    glossary: dict[str, str] | None
    characters: dict[str, str] | None
    rules: list[str] | None
    dub: bool
    voice: str | None
    tts_engine: str
    workdir: str  # temp/{job_id}
    stt_model: str = "large-v3"
    stt_device: str = "auto"
    stt_compute_type: str | None = None
    stt_cpu_threads: int | None = None
    stt_mode: str = "auto"
    stt_batch_size: int = 2
    chunks_total: int = 0
    cancel: Any = None
    on_progress: Callable[..., None] | None = None
    #: One shared translation provider for the whole run (built lazily at most
    #: once, then reused by every translate worker — never one provider per
    #: chunk, which would spawn/tear down a llama-server per chunk for local
    #: models). Stopped once by the orchestrator after the pools drain.
    translation_provider: Any = None


#: Guards lazy construction of the run's shared translation provider so a cold
#: translate pool start cannot build two providers (or double-start servers).
_PROVIDER_LOCK = threading.Lock()


def _ensure_translation_provider(ctx: ChunkPipelineContext) -> Any:
    """Return the run's shared translation provider, constructing it once."""
    provider = ctx.translation_provider
    if provider is not None:
        return provider
    from src.api.pipeline import build_translation_provider  # noqa: PLC0415 - lazy

    with _PROVIDER_LOCK:
        provider = ctx.translation_provider
        if provider is None:
            provider = build_translation_provider(ctx.provider, ctx.provider_config, ctx.api_key)
            ctx.translation_provider = provider
    return provider


def _stop_translation_provider(ctx: ChunkPipelineContext) -> None:
    """Release process-level resources owned by the run's provider (e.g. a
    spawned llama-server) exactly once after the pipeline drains. Idempotent:
    the provider is cleared on the first stop so repeated calls are no-ops."""
    provider = ctx.translation_provider
    if provider is None:
        return
    ctx.translation_provider = None
    stop = getattr(provider, "stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:  # noqa: BLE001 - never mask a pipeline failure
            logger.exception("failed to stop the shared translation provider")


def _check_cancel(ctx: ChunkPipelineContext) -> None:
    if ctx.cancel is not None and ctx.cancel.is_cancelled():
        raise ChunkFailedError("cancelled")


def _run_stt_stage(state: _StageState, ctx: ChunkPipelineContext) -> None:
    """Slice the chunk's audio and transcribe it. A genuinely silent logical
    window is a VALID outcome: ``state.segments`` stays empty and the chunk
    contributes nothing instead of failing the run."""
    from src.services.stt_service import (  # noqa: PLC0415
        E_STT_NO_SPEECH,
        STTError,
        STT_MODE_BATCHED,
        STT_MODE_REGULAR,
        transcribe as stt_transcribe,
    )

    chunk = state.chunk
    chunk_dir = os.path.join(ctx.workdir, "chunks", chunk.chunk_id)
    os.makedirs(chunk_dir, exist_ok=True)
    audio_path = os.path.join(chunk_dir, "audio.wav")
    state.audio_path = audio_path
    perf = state.perf
    if perf["wall_start_s"] == 0.0:
        perf["wall_start_s"] = time.monotonic()

    perf["slice_start_s"] = time.monotonic()
    slice_audio(ctx.source_audio, audio_path, chunk.start, chunk.duration)
    perf["slice_s"] = time.monotonic() - perf["slice_start_s"]
    _check_cancel(ctx)

    def _progress(ratio: float, message: str) -> None:
        """Map a per-chunk (0..1) ratio to the global chunked-pipeline fraction
        (chunk.index is 1-based) so the overall progress advances continuously
        across the whole run instead of jumping per stage/chunk."""
        if ctx.on_progress:
            ctx.on_progress(
                (chunk.index - 1 + max(0.0, min(1.0, ratio))) / (ctx.chunks_total or 1),
                "chunk-stt",
                message,
                chunk_index=chunk.index,
                total_chunks=ctx.chunks_total,
            )

    _progress(0.0, f"STT_STARTED {chunk.chunk_id}")
    perf["stt_start_s"] = time.monotonic()

    stt_log_lines: list[str] = []

    def _stt_log(msg: str) -> None:
        stt_log_lines.append(msg)

    try:
        result = stt_transcribe(
            audio_path,
            project_id=ctx.project_id,
            model_name=ctx.stt_model,
            device=ctx.stt_device,
            compute_type=ctx.stt_compute_type,
            cpu_threads=ctx.stt_cpu_threads,
            language=ctx.source_language,
            total_duration_seconds=chunk.duration,
            cancel=ctx.cancel,
            stt_mode=ctx.stt_mode,
            batch_size=ctx.stt_batch_size,
            on_stt_log=_stt_log,
            on_progress=lambda ratio: _progress(ratio, f"STT {chunk.chunk_id}"),
        )
        raw_segments = result.transcript.get("segments", [])
    except STTError as exc:
        if exc.code == E_STT_NO_SPEECH:
            raw_segments = []
        else:
            raise ChunkFailedError(f"{chunk.chunk_id}: STT failed: {exc.message}") from exc
    perf["stt_s"] = time.monotonic() - perf["stt_start_s"]
    rtf = perf["stt_s"] / chunk.duration if chunk.duration > 0 else 0.0
    _stt_log(f"[STT] Mode: {result.engine}")
    _stt_log(f"[STT] Model: {result.model_used}")
    _stt_log(f"[STT] Batch size: {ctx.stt_batch_size}")
    _stt_log(f"[STT] Chunk: {chunk.index}/{ctx.chunks_total}")
    _stt_log(f"[STT] RTF: {rtf:.3f}")
    _progress(1.0, f"STT_COMPLETED {chunk.chunk_id}")
    _check_cancel(ctx)

    # Shift slice-relative timestamps to the source timeline, clamp to the
    # logical window (overlap is context only). Every artifact carries a
    # STABLE per-chunk identity (``chunk_id`` + ``src_idx``) so assembly pairs
    # a segment with its translation by *identity*, never by a positional
    # ``seg_N`` renumber that can drift when one side drops a row (FIX #1,
    # review 2026-08-18).
    segments: list[dict[str, Any]] = []
    for i, seg in enumerate(raw_segments):
        g_start = chunk.start + float(seg["start"])
        g_end = chunk.start + float(seg["end"])
        clamped = clamp_to_logical(g_start, g_end, chunk.logical_start, chunk.logical_end)
        if clamped is None:
            continue
        lo, hi = clamped
        segments.append(
            {
                "chunk_id": chunk.chunk_id,
                "src_idx": i,
                "start": round(lo, 3),
                "end": round(hi, 3),
                "text": seg["text"],
                "language": seg.get("language", ctx.source_language or "und"),
                "confidence": seg.get("confidence"),
                "speaker": seg.get("speaker"),
            }
        )

    # P1 STT quality guard (2026-08-20): a batched early-EOS collapse swallows
    # a large interior block of speech while the transcript still *looks*
    # valid (monotonic, in-window). Acoustic check catches it and re-runs this
    # chunk once in ``regular`` mode, which covers the block fully.
    if (
        getattr(result, "engine", None) == STT_MODE_BATCHED
        and segments
        and _segment_gap_has_speech(
            audio_path,
            chunk.logical_start,
            chunk.logical_end,
            segments,
            time_offset=chunk.start,
        )
    ):
        _stt_log("[STT-GUARD] batched output has a large speech-carrying gap; re-running in regular mode")
        logger.warning(
            "STT quality guard: %s swallowed speech; re-running STT in regular mode",
            chunk.chunk_id,
        )
        perf["stt_guard_fallback_s"] = time.monotonic()
        try:
            result = stt_transcribe(
                audio_path,
                project_id=ctx.project_id,
                model_name=ctx.stt_model,
                device=ctx.stt_device,
                compute_type=ctx.stt_compute_type,
                cpu_threads=ctx.stt_cpu_threads,
                language=ctx.source_language,
                total_duration_seconds=chunk.duration,
                cancel=ctx.cancel,
                stt_mode=STT_MODE_REGULAR,
                on_stt_log=_stt_log,
            )
            fallback_segments = result.transcript.get("segments", [])
        except STTError as exc:
            if exc.code == E_STT_NO_SPEECH:
                fallback_segments = []
            else:
                raise ChunkFailedError(f"{chunk.chunk_id}: STT fallback failed: {exc.message}") from exc
        perf["stt_guard_fallback_s"] = time.monotonic() - perf["stt_guard_fallback_s"]
        rebuild: list[dict[str, Any]] = []
        for i, seg in enumerate(fallback_segments):
            g_start = chunk.start + float(seg["start"])
            g_end = chunk.start + float(seg["end"])
            clamped = clamp_to_logical(g_start, g_end, chunk.logical_start, chunk.logical_end)
            if clamped is None:
                continue
            lo, hi = clamped
            rebuild.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "src_idx": i,
                    "start": round(lo, 3),
                    "end": round(hi, 3),
                    "text": seg["text"],
                    "language": seg.get("language", ctx.source_language or "und"),
                    "confidence": seg.get("confidence"),
                    "speaker": seg.get("speaker"),
                }
            )
        if rebuild:
            segments = rebuild
        else:
            _stt_log("[STT-GUARD] fallback produced no usable segments; keeping batched result")

    state.segments = segments
    state.silent = not segments


def _run_translate_stage(state: _StageState, ctx: ChunkPipelineContext) -> None:
    """Translate the chunk's segments (real provider abstraction).

    Idempotent: re-running it (a retry) reuses the chunk's STT result instead
    of re-transcribing. Silent chunks skip translation entirely.
    """
    if not state.segments:
        return
    from src.services.providers.base import SourceSegment  # noqa: PLC0415
    from src.services.translation_service import TranslationService  # noqa: PLC0415

    chunk = state.chunk
    perf = state.perf

    def _progress(ratio: float, message: str) -> None:
        if ctx.on_progress:
            ctx.on_progress(
                (chunk.index - 1 + max(0.0, min(1.0, ratio))) / (ctx.chunks_total or 1),
                "chunk-translate",
                message,
                chunk_index=chunk.index,
                total_chunks=ctx.chunks_total,
            )

    _progress(0.0, f"TRANSLATION_STARTED {chunk.chunk_id}")
    perf["translate_start_s"] = time.monotonic()
    provider = _ensure_translation_provider(ctx)
    service = TranslationService()
    sources = [
        SourceSegment(idx=i, segment_id=f"seg_{i}", text=s["text"], speaker=s.get("speaker"))
        for i, s in enumerate(state.segments)
    ]
    blocks = service.translate_segments(
        sources,
        target_language=ctx.target_language,
        provider=provider,
        model=ctx.model,
        glossary_ver=ctx.glossary_ver,
        glossary=ctx.glossary,
        characters=ctx.characters,
        rules=ctx.rules,
        cancel=ctx.cancel,
        on_progress=lambda ratio: _progress(ratio, f"TRANSLATE {chunk.chunk_id}"),
    )
    perf["translate_s"] = time.monotonic() - perf["translate_start_s"]
    translated_by_idx: dict[int, str] = {}
    for block in blocks:
        for item in block.translations:
            translated_by_idx[item.idx] = item.translated_text
    _progress(1.0, f"TRANSLATION_COMPLETED {chunk.chunk_id}")

    cues: list[dict[str, Any]] = []
    for i, seg in enumerate(state.segments):
        text = translated_by_idx.get(i, "").strip()
        if not text:
            continue
        cues.append(
            {
                "chunk_id": seg.get("chunk_id"),
                "src_idx": seg.get("src_idx"),
                "start": seg["start"],
                "end": seg["end"],
                "source_text": seg["text"],
                "text": text,
                "speaker": seg.get("speaker"),
            }
        )
    state.cues = cues
    if not cues:
        raise ChunkFailedError(f"{chunk.chunk_id}: translation produced no text")


def _run_tts_stage(state: _StageState, ctx: ChunkPipelineContext) -> None:
    """Synthesize the chunk's voice track (only when dubbing is on)."""
    if not ctx.dub or not state.segments:
        return
    from src.services.tts_service import TTSCue, synthesize_cues  # noqa: PLC0415

    chunk = state.chunk
    perf = state.perf
    chunk_dir = os.path.dirname(state.audio_path)

    def _progress(ratio: float, message: str) -> None:
        if ctx.on_progress:
            ctx.on_progress(
                (chunk.index - 1 + max(0.0, min(1.0, ratio))) / (ctx.chunks_total or 1),
                "chunk-tts",
                message,
                chunk_index=chunk.index,
                total_chunks=ctx.chunks_total,
            )

    _progress(0.0, f"TTS_STARTED {chunk.chunk_id}")
    perf["tts_start_s"] = time.monotonic()
    local_cues = [
        TTSCue(
            start=round(c["start"] - chunk.logical_start, 3),
            end=round(c["end"] - chunk.logical_start, 3),
            text=c["text"],
        )
        for c in state.cues
    ]
    tts_result = synthesize_cues(
        local_cues,
        voice=ctx.voice,
        engine=ctx.tts_engine,
        language=ctx.target_language,
        duration_seconds=chunk.logical_duration,
        output_dir=chunk_dir,
        cancel=ctx.cancel,
        on_progress=lambda ratio: _progress(ratio, f"TTS {chunk.chunk_id}"),
    )
    state.voice_track = tts_result.voice_track_path
    perf["tts_s"] = time.monotonic() - perf["tts_start_s"]
    _progress(1.0, f"TTS_COMPLETED {chunk.chunk_id}")


def _build_chunk_artifacts(state: _StageState, ctx: ChunkPipelineContext) -> ChunkArtifacts:
    """Materialize a finished chunk. Wall-clock ends here; silent chunks are a
    valid outcome and contribute nothing to timeline/subtitles/voice."""
    return ChunkArtifacts(
        index=state.chunk.index,
        chunk_id=state.chunk.chunk_id,
        logical_start=state.chunk.logical_start,
        logical_end=state.chunk.logical_end,
        segments=state.segments,
        cues=state.cues,
        voice_track=state.voice_track,
        audio_path=state.audio_path,
        silent=state.silent,
        perf={**state.perf, "wall_end_s": time.monotonic(), "queue_wait_s": {**state.queue_wait}},
    )


def process_one_chunk(chunk: Chunk, ctx: ChunkPipelineContext) -> ChunkArtifacts:
    """Run one chunk through STT → translation → TTS (coupled entry point).

    Historic path kept for the bounded ``ChunkScheduler`` tests. The production
    pipeline uses :class:`StreamingChunkPipeline`, which runs the same stage
    helpers across separate bounded worker pools instead of holding one slot
    for a whole chunk. Raises :class:`ChunkFailedError` so the caller retries.
    """
    state = _StageState(chunk=chunk)
    try:
        _run_stt_stage(state, ctx)
        _run_translate_stage(state, ctx)
        _run_tts_stage(state, ctx)
    except ChunkFailedError:
        raise
    except Exception as exc:  # noqa: BLE001 - any per-chunk failure retries the chunk
        raise ChunkFailedError(f"{chunk.chunk_id}: {exc}") from exc
    return _build_chunk_artifacts(state, ctx)


# ---------------------------------------------------------------------------
# Orchestrator — the whole chunked pipeline (used by the API endpoint)
# ---------------------------------------------------------------------------


def build_performance_trace(
    per_chunk: Sequence[ChunkArtifacts],
    *,
    job_id: str,
    total_duration: float,
    chunk_duration: float,
    overlap: float,
    max_concurrency: int,
    max_retries: int,
    stt_workers: int | None = None,
    translate_workers: int | None = None,
    tts_workers: int | None = None,
) -> dict[str, Any]:
    """Derive a per-stage performance trace from the measured chunk timings.

    Pure arithmetic over the wall-clock offsets the chunk processor recorded in
    ``ChunkArtifacts.perf`` — no timers are started here, so the function is
    deterministic and unit-testable without touching media/network.

    Concurrency for a stage is the number of chunks whose *stage interval*
    overlaps at a given instant. ``peak_active`` is the max overlap; ``avg_active``
    is total stage-seconds divided by pipeline wall time — that ratio versus
    ``max_concurrency`` answers "was the stage the bottleneck, and did the pool
    actually stay full in it?" Tables both per-chunk rows and per-stage totals for
    the PERFORMANCE_TRACE report.
    """
    stages = ("slice", "stt", "translate", "tts")
    rows: list[dict[str, Any]] = []
    intervals: dict[str, list[tuple[float, float]]] = {s: [] for s in stages}
    chunk_intervals: list[tuple[float, float]] = []
    waits: dict[str, list[float]] = {s: [] for s in stages}

    measured = [a for a in per_chunk if a.perf is not None]
    baseline = min((a.perf["wall_start_s"] for a in measured), default=0.0)
    wall_end = max((a.perf["wall_end_s"] for a in measured), default=baseline)
    elapsed_s = max(0.0, wall_end - baseline)

    for art in measured:
        p = art.perf
        chunk_intervals.append((p["wall_start_s"], p["wall_end_s"]))
        row: dict[str, Any] = {
            "index": art.index,
            "chunk_id": art.chunk_id,
            "start_ms": round((p["wall_start_s"] - baseline) * 1000),
            "end_ms": round((p["wall_end_s"] - baseline) * 1000),
        }
        chunk_wait = p.get("queue_wait_s", {}) or {}
        for stage in stages:
            dur = p.get(f"{stage}_s", 0.0)
            row[f"{stage}_ms"] = round(dur * 1000)
            if dur > 0:
                intervals[stage].append((p[f"{stage}_start_s"], p[f"{stage}_start_s"] + dur))
                row[f"{stage}_start_ms"] = round((p[f"{stage}_start_s"] - baseline) * 1000)
                row[f"{stage}_end_ms"] = round((p[f"{stage}_start_s"] - baseline) * 1000 + dur * 1000)
            wait = max(0.0, chunk_wait.get(stage, 0.0))
            row[f"queue_wait_{stage}_ms"] = round(wait * 1000)
            if wait > 0:
                waits[stage].append(wait)
        rows.append(row)

    def _peak_avg(active: list[tuple[float, float]]) -> dict[str, Any]:
        if not active or elapsed_s <= 0:
            return {"peak_active": 0, "avg_active": 0.0}
        events: list[tuple[float, int]] = []
        for start, end in active:
            events.append((start, 1))
            events.append((end, -1))
        events.sort(key=lambda t: (t[0], t[1]))
        cur = peak = 0
        for _, delta in events:
            cur += delta
            peak = max(peak, cur)
        return {
            "peak_active": peak,
            "avg_active": round(sum(end - start for start, end in active) / elapsed_s, 2),
        }

    chunk_peak, chunk_avg = _peak_avg(chunk_intervals).values()
    stages_report = {
        stage: {
            "total_ms": round(sum(end - start for start, end in intervals[stage]) * 1000),
            "chunks_ran": len(intervals[stage]),
            "total_queue_ms": round(sum(waits[stage]) * 1000),
            "avg_queue_ms": round(sum(waits[stage]) / len(waits[stage]) * 1000, 1)
            if waits[stage]
            else 0,
            **_peak_avg(intervals[stage]),
        }
        for stage in stages
    }

    return {
        "schema_version": 1,
        "job_id": job_id,
        "config": {
            "total_duration_s": total_duration,
            "chunk_duration_s": chunk_duration,
            "overlap_s": overlap,
            "max_concurrency": max_concurrency,
            "stt_workers": stt_workers if stt_workers is not None else max_concurrency,
            "translate_workers": translate_workers if translate_workers is not None else max_concurrency,
            "tts_workers": tts_workers if tts_workers is not None else max_concurrency,
            "max_retries": max_retries,
            "total_chunks": len(per_chunk),
            "measured_chunks": len(measured),
        },
        "chunk_level": {"peak_active": chunk_peak, "avg_active": chunk_avg},
        "wall_elapsed_s": round(elapsed_s, 2),
        "stages": stages_report,
        "chunks": rows,
    }


def run_chunked_pipeline(
    *,
    job_id: str,
    project_id: str,
    project_dir: str,
    source_video: str,
    source_audio: str,
    target_language: str,
    source_language: str | None,
    provider: str,
    provider_config: dict[str, str] | None,
    api_key: str | None,
    model: str,
    glossary_ver: str,
    glossary: dict[str, str] | None,
    characters: dict[str, str] | None,
    rules: list[str] | None,
    dub: bool,
    voice: str | None,
    tts_engine: str,
    stt_model: str = "large-v3",
    stt_device: str = "auto",
    stt_mode: str = "auto",
    stt_batch_size: int = 2,
    chunk_duration: float = DEFAULT_CHUNK_DURATION,
    overlap: float = DEFAULT_OVERLAP,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    stt_workers: int | None = None,
    translate_workers: int | None = None,
    tts_workers: int | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    duration_tolerance: float = DEFAULT_DURATION_TOLERANCE,
    cancel: Any = None,
    on_progress: Callable[[float, str, str | None], None] | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Run the full chunked pipeline and return the manifest + artifact paths.

    Writes the merged artifacts (transcript/translation/subtitles/voice track)
    into ``project_dir/cache`` exactly where the later stages expect them, so
    the existing render stage works unchanged.
    """
    from src.services.media_service import probe  # noqa: PLC0415 - lazy

    meta = probe(source_video)
    total_duration = float(meta.duration or 0.0)
    if total_duration <= 0:
        raise ChunkFailedError("cannot chunk: source duration unknown")

    if on_event:
        on_event("info", f"JOB_STARTED {job_id}")
    # Pipeline performance profile: wall-seconds of the post-STT phases
    # (validation / assembly / subtitle / file I/O). Stages measured by the
    # scheduler (slice/stt/translate/tts) live in ``build_performance_trace``.
    phases: dict[str, float] = {}
    manager = ChunkManager(
        total_duration, chunk_duration=chunk_duration, overlap=overlap
    )
    if on_event:
        on_event("info", f"CHUNK_CREATED {len(manager.chunks)} chunks ({chunk_duration:g}s, overlap {overlap:g}s)")

    order_issues = validate_chunk_order(manager.chunks)
    if order_issues:
        raise ChunkFailedError("chunk order invalid: " + "; ".join(order_issues))

    ctx = ChunkPipelineContext(
        project_id=project_id,
        project_dir=project_dir,
        source_audio=source_audio,
        source_language=source_language,
        target_language=target_language,
        provider=provider,
        provider_config=provider_config,
        api_key=api_key,
        model=model,
        glossary_ver=glossary_ver,
        glossary=glossary,
        characters=characters,
        rules=rules,
        dub=dub,
        voice=voice,
        tts_engine=tts_engine,
        workdir=os.path.join(project_dir, "temp", job_id),
        stt_model=stt_model,
        stt_device=stt_device,
        stt_mode=stt_mode,
        stt_batch_size=stt_batch_size,
        chunks_total=len(manager.chunks),
        cancel=cancel,
        on_progress=on_progress,
    )

    # Per-call CPU thread budget for the STT stage: on CPU faster-whisper
    # defaults to *all* cores per transcribe() call, so concurrent chunks used
    # to oversubscribe the machine N×. Budget cores // stt_workers so the pool
    # really parallelizes (see stt_thread_budget).
    effective_stt_workers = stt_workers or max_concurrency
    ctx.stt_cpu_threads = stt_thread_budget(effective_stt_workers)

    cleanup = CleanupManager(ctx.workdir)

    def _event(level: str, message: str) -> None:
        if on_event:
            on_event(level, message)

    scheduler = StreamingChunkPipeline(
        manager=manager,
        ctx=ctx,
        stt_workers=effective_stt_workers,
        translate_workers=translate_workers or max_concurrency,
        tts_workers=tts_workers or max_concurrency,
        max_retries=max_retries,
        on_event=_event,
        on_progress=on_progress,
    )
    try:
        per_chunk = scheduler.run()
    except ChunkFailedError:
        cleanup.keep_temp()
        raise
    finally:
        # The translate pool is fully drained here — release one-shot provider
        # resources (a spawned llama-server, etc.) exactly once per run.
        _stop_translation_provider(ctx)

    # validate every chunk's artifacts
    _ph = time.monotonic()
    for art in per_chunk:
        issues = validate_chunk_result(art)
        if issues:
            cleanup.keep_temp()
            raise ChunkFailedError(
                f"chunk {art.chunk_id} validation failed: " + "; ".join(issues)
            )
    phases["validate_chunks"] = time.monotonic() - _ph

    cleanup.transition("assembling")
    if on_event:
        on_event("info", "ASSEMBLY_STARTED")

    # PHASE 10 — ordered assembly. The MERGED TRANSCRIPT is the single
    # canonical source of identity: translation blocks and subtitle cues are
    # built FROM it, matched per segment by identity — never re-numbered from
    # a separate cue list that can dedup differently and shift every ``seg_N``
    # after the first dropped row (FIX #1, review 2026-08-18).
    _ph = time.monotonic()
    segments = merge_segments(per_chunk)
    phases["merge_segments"] = time.monotonic() - _ph

    # Pair translated text to its source segment by the stable (chunk_id,
    # src_idx) identity (FIX #1, review 2026-08-18).
    _ph = time.monotonic()
    translated = assemble_translations(segments, per_chunk)
    phases["assemble_translations"] = time.monotonic() - _ph

    # timeline validation before writing anything (Phase 11)
    _ph = time.monotonic()
    timeline_issues = validate_timeline(manager.chunks, segments, total_duration, tolerance=duration_tolerance)
    if timeline_issues:
        cleanup.keep_temp()
        raise ChunkFailedError("timeline validation failed: " + "; ".join(timeline_issues))
    phases["validate_timeline"] = time.monotonic() - _ph

    cache_dir = os.path.join(project_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    # Performance trace (per-chunk per-stage timings + concurrency utilization).
    # Lives next to the manifest in the project cache where it survives cleanup.
    trace = build_performance_trace(
        per_chunk,
        job_id=job_id,
        total_duration=total_duration,
        chunk_duration=chunk_duration,
        overlap=overlap,
        max_concurrency=max_concurrency,
        max_retries=max_retries,
        stt_workers=stt_workers or max_concurrency,
        translate_workers=translate_workers or max_concurrency,
        tts_workers=tts_workers or max_concurrency,
    )
    s = trace["stages"]
    logger.info(
        "PERF %d chunks wall=%.1fs | stt: %d/%s | translate: %d/%s | tts: %d/%s",
        trace["config"]["total_chunks"],
        trace["wall_elapsed_s"],
        s["stt"]["peak_active"],
        s["stt"]["avg_active"],
        s["translate"]["peak_active"],
        s["translate"]["avg_active"],
        s["tts"]["peak_active"],
        s["tts"]["avg_active"],
    )

    # Transcript artifact (merged, global timeline)
    _ph = time.monotonic()
    transcript = {
        "schema_version": 1,
        "project_id": project_id,
        "language": source_language or "und",
        "model": stt_model,
        "segments": segments,
    }
    transcript_path = os.path.join(cache_dir, "transcript.json")
    with open(transcript_path, "w", encoding="utf-8") as fh:
        json.dump(transcript, fh, ensure_ascii=False)

    # Translation artifact (merged blocks) — same ids as the transcript.
    blocks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for i, (seg, text) in enumerate(zip(segments, translated)):
        current.append(
            {
                "idx": i,
                "segment_id": seg["id"],
                "source_text": seg["text"],
                "translated_text": text,
                "confidence": 1.0,
            }
        )
        if len(current) == 10:
            blocks.append({"block_idx": len(blocks), "translations": current})
            current = []
    if current:
        blocks.append({"block_idx": len(blocks), "translations": current})
    translation = {
        "schema_version": 1,
        "target_language": target_language,
        "model": model,
        "blocks": blocks,
    }
    translation_path = os.path.join(cache_dir, "translation.json")
    with open(translation_path, "w", encoding="utf-8") as fh:
        json.dump(translation, fh, ensure_ascii=False)
    phases["write_artifacts"] = time.monotonic() - _ph

    # Subtitle files (reuse the existing SubtitleService generator)
    _ph = time.monotonic()
    from src.services.subtitle_service import CueSource, SubtitleService  # noqa: PLC0415

    segments_out = [
        CueSource(
            idx=i,
            segment_id=seg["id"],
            start=seg["start"],
            end=seg["end"],
            text=text,
            speaker=seg.get("speaker"),
        )
        for i, (seg, text) in enumerate(zip(segments, translated))
    ]
    doc = SubtitleService().generate(
        segments_out,
        project_id=project_id,
        language=target_language,
        output_dir=cache_dir,
    )
    subtitle_srt = os.path.join(cache_dir, "subtitle.srt")
    subtitle_ass = os.path.join(cache_dir, "subtitle.ass")
    with open(subtitle_srt, "w", encoding="utf-8") as fh:
        fh.write(doc.srt_content)
    with open(subtitle_ass, "w", encoding="utf-8") as fh:
        fh.write(doc.ass_content)
    phases["generate_subtitles"] = time.monotonic() - _ph

    # Voice track — the streaming pipeline already built it in order while
    # processing (per-chunk samples streamed into one PCM file as chunks were
    # committed) but into the TEMP tree; move it to the durable project cache
    # (same location as the concat fallback) before finalize wipes temp.
    voice_track: str | None = None
    if dub:
        _ph = time.monotonic()
        if scheduler.voice_track_path:
            cache_track = os.path.join(cache_dir, "voice_track.wav")
            shutil.move(scheduler.voice_track_path, cache_track)
            voice_track = cache_track
        else:
            voice_track = concat_voice_tracks(
                [art.voice_track for art in sorted(per_chunk, key=lambda a: a.index)],
                [art.logical_end - art.logical_start for art in sorted(per_chunk, key=lambda a: a.index)],
                os.path.join(cache_dir, "voice_track.wav"),
            )
        if on_event:
            on_event("info", "ASSEMBLY_COMPLETED voice track + subtitles")
        phases["voice_assembly"] = time.monotonic() - _ph

    if on_event:
        on_event("info", "ASSEMBLY_COMPLETED")

    cleanup.transition("validating")
    _ph = time.monotonic()
    manifest = manager.manifest(
        job_id=job_id,
        source_video=source_video,
        completed_chunks=len(per_chunk),
        failed_chunks=[],
    )
    # The manifest survives cleanup (Phase 16 keeps the manifest + output): it
    # lives in the project cache, not the removable temp tree, so a finished
    # run can still reconstruct the pipeline state.
    manifest_path = os.path.join(cache_dir, f"chunk_manifest_{job_id}.json")
    os.makedirs(cache_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    phases["write_manifest"] = time.monotonic() - _ph

    # Complete the pipeline performance profile (post-STT phases measured
    # above) and persist it next to the manifest before cleanup runs.
    trace["phases"] = {k: round(v, 3) for k, v in phases.items()}
    trace["phases_wall_s"] = round(sum(phases.values()), 3)
    trace_path = os.path.join(cache_dir, f"performance_trace_{job_id}.json")
    with open(trace_path, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, ensure_ascii=False, indent=2)

    cleanup.transition("output_ready")
    manifest["manifest_path"] = manifest_path
    manifest["trace_path"] = trace_path
    manifest["perf"] = {
        "wall_elapsed_s": trace["wall_elapsed_s"],
        "config": trace["config"],
        "chunk_level": trace["chunk_level"],
        "stages": trace["stages"],
        "chunks": trace["chunks"],
        "phases": trace["phases"],
        "phases_wall_s": trace["phases_wall_s"],
    }
    manifest["total_duration"] = total_duration
    manifest["stt"] = {
        "mode": stt_mode,
        "batch_size": stt_batch_size,
        "model": stt_model,
    }
    manifest["artifacts"] = {
        "transcript": transcript_path,
        "translation": translation_path,
        "subtitle_srt": subtitle_srt,
        "subtitle_ass": subtitle_ass,
        "voice_track": voice_track,
    }
    return manifest
