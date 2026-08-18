"""Render a chunked-pipeline performance trace into PERFORMANCE_TRACE.md.

Reads the structured per-chunk per-stage trace that the worker writes
(``cache/performance_trace_<job_id>.json``) and renders a human-readable,
numbers-only report: per-stage totals + concurrency utilization, the slowest
chunks per stage, and the full per-chunk table. It only ever formats numbers
that were actually measured — it does not fabricate or extrapolate.

Usage:

    py scripts/perf_report.py --trace cache/performance_trace_job.json
    py scripts/perf_report.py --out PERFORMANCE_TRACE.md   (latest trace found)
"""

from __future__ import annotations

import argparse
import glob
from datetime import datetime, timezone
from pathlib import Path


def _fmt_ms(ms: int) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.2f}s"
    return f"{ms}ms"


def _top_by(trace: dict, key: str, n: int = 8) -> list[dict]:
    ranks = sorted(
        (r for r in trace["chunks"] if r.get(f"{key}_ms", 0) > 0),
        key=lambda r: r[f"{key}_ms"],
        reverse=True,
    )
    return ranks[:n]


def render(trace: dict) -> str:
    cfg = trace["config"]
    lines: list[str] = []
    lines.append("# PERFORMANCE_TRACE")
    lines.append("")
    lines.append(f"- Job: `{trace['job_id']}`")
    lines.append(
        f"- Config: {cfg['total_chunks']} chunks | {cfg['chunk_duration_s']:g}s chunk"
        f" | {cfg['overlap_s']:g}s overlap | max_concurrency={cfg['max_concurrency']}"
        f" | max_retries={cfg['max_retries']}"
    )
    realtime = cfg["total_duration_s"] / trace["wall_elapsed_s"] if trace["wall_elapsed_s"] > 0 else 0.0
    lines.append(
        f"- Pipeline wall time: **{_fmt_ms(round(trace['wall_elapsed_s'] * 1000))}**"
        f" (source video {cfg['total_duration_s']:g}s -> **{realtime:.1f}x realtime**)"
    )
    lines.append(
        f"- Chunk-level concurrency: peak {trace['chunk_level']['peak_active']}/{cfg['max_concurrency']},"
        f" avg {trace['chunk_level']['avg_active']:.2f}/{cfg['max_concurrency']}"
    )
    lines.append(f"- Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z")
    lines.append("")

    lines.append("## Stage utilization")
    lines.append("")
    lines.append("| stage | total | chunks_ran | peak_active | avg_active | vs max_workers |")
    lines.append("|---|---|---:|---:|---:|---:|")
    total_parallel = max(1, trace["wall_elapsed_s"])
    for stage, s in trace["stages"].items():
        util = 100.0 * s["avg_active"] / cfg["max_concurrency"]
        lines.append(
            f"| {stage} | {_fmt_ms(s['total_ms'])} | {s['chunks_ran']} | {s['peak_active']} |"
            f" {s['avg_active']:.2f} | {util:.0f}% |"
        )
    lines.append("")
    lines.append(
        "A stage with `avg_active` near `max_concurrency` is the pipeline's parallel hot loop; "
        "a stage with `avg_active << 1` was nearly idle in wall-clock terms."
    )
    lines.append("")

    lines.append("## Slowest chunks")
    lines.append("")
    for stage in ("stt", "translate", "tts"):
        lines.append(f"### {stage}")
        lines.append("")
        top = _top_by(trace, stage)
        if not top:
            lines.append("_no chunks ran this stage_")
        else:
            lines.append("| chunk | start_ms | stage | wall |")
            lines.append("|---|---:|---:|---:|")
            for r in top:
                lines.append(
                    f"| {r['chunk_id']} | {r['start_ms']} | {_fmt_ms(r[f'{stage}_ms'])} |"
                    f" {_fmt_ms(r['end_ms'] - r['start_ms'])} |"
                )
        lines.append("")

    lines.append("## Full per-chunk trace")
    lines.append("")
    lines.append("| chunk | index | start_ms | end_ms | slice | stt | translate | tts |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in trace["chunks"]:
        lines.append(
            f"| {r['chunk_id']} | {r['index']} | {r['start_ms']} | {r['end_ms']} |"
            f" {_fmt_ms(r['slice_ms'])} | {_fmt_ms(r['stt_ms'])} |"
            f" {_fmt_ms(r['translate_ms'])} | {_fmt_ms(r['tts_ms'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _find_latest_trace() -> Path | None:
    matches = sorted(
        Path("cache").glob("performance_trace_*.json") if Path("cache").exists() else [],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", default=None, help="path to performance_trace_*.json")
    parser.add_argument("--out", default="PERFORMANCE_TRACE.md")
    args = parser.parse_args()

    trace_path = Path(args.trace) if args.trace else _find_latest_trace()
    if trace_path is None or not trace_path.exists():
        print("no trace found; pass --trace cache/performance_trace_<job_id>.json")
        return 1

    import json  # noqa: PLC0415 - stdlib, keep imports top-of-function lean

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    out_path = Path(args.out)
    out_path.write_text(render(trace), encoding="utf-8")
    print(f"written {out_path} from {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())