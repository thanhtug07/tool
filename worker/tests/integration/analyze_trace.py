"""Execution-timeline analysis for the chunked pipeline's perf trace.

Pure functions over ``manifest["perf"]`` (the ``build_performance_trace``
dict). Never starts timers and never touches media/network, so it is safe to
reuse from any benchmark driver or CI check.

Features:

1. ``gantt`` — ASCII Gantt of every chunk × stage (slice/s/stt/t/translate/x/
   tts) against pipeline wall time, so stage overlap is visible in one
   picture. Guards the "STT/TL/TTS must overlap, not serialize" invariant.
2. ``stage_windows`` — absolute [start, end) wall window per stage, used to
   attribute a resource sample (GPU/CPU/RAM) to the stage that was active
   when it was taken.
3. ``overlap_report`` — pipeline wall vs. the sum of per-stage durations:
   the ratio answers "how much parallelism did the pipeline actually buy
   over a fully serialized run of the same stages".
4. ``concurrency_audit`` — per-stage peak/avg active workers vs. configured
   pool size, answering "was the pool ever the bottleneck?".

All numbers are derived from measured timings only.
"""

from __future__ import annotations

STAGE_LABELS = ("slice", "stt", "translate", "tts")
STAGE_CHARS = {"slice": "S", "stt": "t", "translate": "T", "tts": "X"}


def _buckets(perf: dict, width: int = 60) -> tuple[list[dict], float, float]:
    """Return ``(rows, span_ms, bucket_ms)`` for the gantt."""
    rows = perf.get("chunks", []) or []
    wall_ms = max((r.get("end_ms", 0) for r in rows), default=0) or 0
    span_ms = max(float(wall_ms), 1.0)
    bucket = span_ms / width
    return rows, span_ms, bucket


def gantt(perf: dict, width: int = 60) -> list[str]:
    """ASCII execution timeline: one line per chunk, per-tick stage chars.

    Priority when two stages overlap in the same tick:
    ``stt`` > ``tts`` > ``translate`` > ``slice``. Legend is always printed.
    """
    rows, span_ms, bucket = _buckets(perf, width)
    if not rows:
        return ["(no measured chunks)"]
    priority = ("stt", "tts", "translate", "slice")
    lines = [
        f"execution timeline  x-axis: {span_ms/1000:.2f}s across {width} ticks",
        f"legend  slice=S  stt=t  translate=T  tts=X  (priority stt>tts>translate>slice)",
        f"time   |{'-' * width}|",
    ]

    def col(t_ms: float) -> int:
        return max(0, min(width - 1, int(t_ms / bucket)))

    for row in rows:
        cells = [" "] * width
        for label in priority:
            start = row.get(f"{label}_start_ms")
            end = row.get(f"{label}_end_ms")
            if start is None or end is None:
                continue
            char = STAGE_CHARS[label]
            for c in range(col(start), min(width - 1, col(end)) + 1):
                cells[c] = char
        lines.append(
            f"chunk {row.get('index', '?'):>3} |{''.join(cells)}| "
            f"[{row.get('start_ms', 0)}ms -> {row.get('end_ms', 0)}ms]"
        )
    return lines


def stage_windows(perf: dict) -> dict:
    """Absolute [start_ms, end_ms) window per stage over the pipeline wall."""
    rows = perf.get("chunks", []) or []
    windows: dict[str, dict] = {}
    for label in STAGE_LABELS:
        starts = [r[f"{label}_start_ms"] for r in rows if r.get(f"{label}_start_ms") is not None]
        ends = [r[f"{label}_end_ms"] for r in rows if r.get(f"{label}_end_ms") is not None]
        if starts:
            windows[label] = {
                "start_ms": min(starts),
                "end_ms": max(ends),
            }
    return windows


def overlap_report(perf: dict) -> dict:
    """Pipeline wall vs. sum-of-stage-durations (serialization penalty)."""
    stages = perf.get("stages", {}) or {}
    wall = float(perf.get("wall_elapsed_s", 0) or 0)
    stage_totals = {
        label: {"total_ms": stages.get(label, {}).get("total_ms", 0)}
        for label in STAGE_LABELS
    }
    serial_ms = sum(v["total_ms"] for v in stage_totals.values())
    return {
        "wall_ms": round(wall * 1000),
        "serial_index_ms": serial_ms,
        "overlap_factor": round(serial_ms / (wall * 1000), 2) if wall > 0 else 0.0,
        "stage_totals": stage_totals,
    }


def concurrency_audit(perf: dict) -> dict:
    """Per-stage peak/avg active workers vs. configured pool size."""
    stages = perf.get("stages", {}) or {}
    config = perf.get("config", {}) or {}
    audit: dict[str, dict] = {}
    for label in STAGE_LABELS:
        s = stages.get(label, {})
        pool = config.get(f"{label}_workers")
        if pool is None:
            pool = config.get("max_concurrency")
        audit[label] = {
            "pool": pool,
            "peak_active": s.get("peak_active", 0),
            "avg_active": s.get("avg_active", 0),
            "queue_total_ms": s.get("total_queue_ms", 0),
        }
    return audit


def resources_per_stage(trace_peaks: dict, windows: dict) -> dict:
    """Attribute resource peaks to the stage that was active when sampled.

    ``trace_peaks`` is a ``MetricSampler.close()`` result (must include its
    ``timeline`` list with ``t_rel_s``). For each sample we find every stage
    whose window contains it; peaks are reported per stage and the worker
    running CPU/RAM is attributed to whichever stage was active then.
    """
    timeline = trace_peaks.get("timeline", []) or []
    if not timeline or not windows:
        return {}
    out: dict[str, dict] = {}
    for label, window in windows.items():
        s0, s1 = window["start_ms"] / 1000.0, window["end_ms"] / 1000.0
        inwin = [s for s in timeline if s0 <= s.get("t_rel_s", -1) < s1]
        out[label] = {
            "samples_in_window": len(inwin),
            "cpu_peak_percent": max((s.get("cpu_percent", 0) for s in inwin), default=None),
            "ram_peak_mb": max((s.get("rss_mb", 0) for s in inwin), default=None),
            "gpu_peak_percent": max(
                (s.get("gpu_percent") for s in inwin if s.get("gpu_percent") is not None),
                default=None,
            ),
            "vram_peak_mb": max(
                (s.get("vram_mb") for s in inwin if s.get("vram_mb") is not None),
                default=None,
            ),
        }
    return out


def summarize(perf: dict, trace_peaks: dict | None = None) -> dict:
    """One-stop summary: gantt lines text, windows, overlap, concurrency,
    and (when sample timeline present) per-stage resource attribution."""
    return {
        "gantt": gantt(perf),
        "windows_ms": stage_windows(perf),
        "overlap": overlap_report(perf),
        "concurrency": concurrency_audit(perf),
        "resources_per_stage": resources_per_stage(trace_peaks, stage_windows(perf))
        if trace_peaks
        else {},
    }