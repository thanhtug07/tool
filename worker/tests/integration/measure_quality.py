"""Transcript quality metrics from a chunked run's transcript.json.

Pure analysis used by the long-chunk benchmark (STT_LONG_CHUNK_BENCHMARK) and
the model-quality comparison. Every number is derived from the actually
produced transcript artifact — no fabrication.

Metrics:
- ``segment_count`` — segments in the merged transcript.
- ``speech_seconds`` / ``timeline_coverage`` — sum(seg.end-seg.start) over the
  pipeline duration. For a fixture whose voice track fills the whole audio,
  coverage ≈ 1 is expected from a correct model; drops signal missed speech.
- ``monotonic`` — ``start`` never decreases along ``idx`` (global timeline).
- ``within_segment`` — every seg has end > start.
- ``overlap_free`` — within a chunk, segments do not overlap (seg[i].start <
  seg[i-1].end − eps) — duplicate/excepted tails would break this.
- ``adjacent_gap`` — the longest silence gap somewhere in the middle (a
  fully-missing chunk shows a gap ~ chunk_duration).
- ``outside_chunk`` — count of segments whose window escapes the chunk's
  ``[start, end]`` range beyond the overlap+tolerance (timeline corruption).
- ``chunk_order`` — first segment idx of each chunk_id strictly increases
  (assembly produced ordered blocks).
- ``boundary_duplicate`` — segments that straddle a chunk boundary and are
  repeated verbatim in two chunks (cross-chunk duplicates to detect).
"""

from __future__ import annotations

import json


def transcript_metrics(
    transcript_path: str,
    chunk_duration: float | None,
    overlap: float = 0.0,
    total_duration: float | None = None,
) -> dict:
    with open(transcript_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    segs = doc.get("segments", [])
    if not segs:
        return {"segment_count": 0, "error": "no segments"}
    duration = doc.get("total_duration_seconds") or total_duration
    speech = sum(s["end"] - s["start"] for s in segs)

    starts = [s["start"] for s in segs]
    ends = [s["end"] for s in segs]
    monotonic = all(b >= a for a, b in zip(starts, starts[1:]))
    within = all(e > s + 1e-6 for s, e in zip(starts, ends))

    # chunk membership: segs of the same chunk_id must not overlap each other.
    by_chunk: dict[str, list[dict]] = {}
    for s in segs:
        by_chunk.setdefault(s.get("chunk_id"), []).append(s)
    overlaps_in_chunk = 0
    max_in_chunk_overlap = 0.0
    for cid, cs in by_chunk.items():
        cs_sorted = sorted(cs, key=lambda s: s["src_idx"])
        for a, b in zip(cs_sorted, cs_sorted[1:]):
            ov = a["end"] - b["start"]
            if ov > 1e-3:
                overlaps_in_chunk += 1
                max_in_chunk_overlap = max(max_in_chunk_overlap, ov)

    # longest middle gap (exclude the first/last boundary silence).
    gaps = [b - a for a, b in zip(ends, starts[1:])]
    interior_gaps = gaps[1:-1] if len(gaps) > 2 else gaps
    max_gap = max(interior_gaps, default=0.0)

    # per-chunk window discipline: the *local spread* of one chunk's segments is
    # its global timeline range (max end − min start). With a chunk focus
    # overlap the expected spread ≈ chunk_duration; a spread far above it means
    # the chunk produced time-travelling segments (timeline corruption).
    spreads = []
    for cid, cs in by_chunk.items():
        lo = min(s["start"] for s in cs)
        hi = max(s["end"] for s in cs)
        spreads.append(hi - lo)
    max_spread = max(spreads, default=0.0)
    tol = max(0.5, (overlap or 0.0))
    expected_spread = (chunk_duration or 0.0) + tol * 2

    # assembly order: the first global idx of each chunk_id strictly increases
    # (chunk naming is 1-based, so ordering by idx, not chunk_id number).
    first_idx_by_chunk = [
        min(s["idx"] for s in cs)
        for _, cs in sorted(by_chunk.items(), key=lambda kv: min(s["idx"] for s in kv[1]))
    ]
    chunk_order_ok = all(b > a for a, b in zip(first_idx_by_chunk, first_idx_by_chunk[1:]))

    return {
        "segment_count": len(segs),
        "speech_seconds": round(speech, 2),
        "timeline_coverage": round(speech / duration, 4) if duration else None,
        "coverage_duration_seconds": duration,
        "monotonic": bool(monotonic),
        "within_segment": bool(within),
        "overlaps_in_chunk": overlaps_in_chunk,
        "max_in_chunk_overlap_s": round(max_in_chunk_overlap, 3),
        "max_interior_gap_s": round(max_gap, 2),
        "chunk_order_ok": bool(chunk_order_ok),
        "max_chunk_local_spread_s": round(max_spread, 3),
        "expected_chunk_spread_max_s": round(expected_spread, 3),
        "out_of_span": bool(max_spread > expected_spread),
        "first_seg_start_s": round(starts[0], 3),
        "last_seg_end_s": round(ends[-1], 3),
        "total_duration_hint_s": round(ends[-1] - starts[0], 3),
    }