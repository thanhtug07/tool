"""Unit tests for ``analyze_trace`` (pure trace analysis helpers)."""

from __future__ import annotations

import sys
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parents[1] / "integration"
sys.path.insert(0, str(INTEGRATION))

import analyze_trace  # noqa: E402


def _perf() -> dict:
    return {
        "wall_elapsed_s": 100.0,
        "config": {
            "max_concurrency": 4,
            "slice_workers": 1,
            "stt_workers": 4,
            "translate_workers": 4,
            "tts_workers": 4,
        },
        "stages": {
            "slice": {"total_ms": 5000, "peak_active": 1, "avg_active": 0.01, "total_queue_ms": 0},
            "stt": {"total_ms": 600000, "peak_active": 4, "avg_active": 4, "total_queue_ms": 0},
            "translate": {"total_ms": 100000, "peak_active": 4, "avg_active": 2, "total_queue_ms": 5000},
            "tts": {"total_ms": 0, "peak_active": 0, "avg_active": 0.0, "total_queue_ms": 0},
        },
        "chunks": [
            {
                "index": 0,
                "chunk_id": "chunk_0000",
                "start_ms": 0,
                "end_ms": 60000,
                "slice_start_ms": 0,
                "slice_end_ms": 100,
                "stt_start_ms": 100,
                "stt_end_ms": 15000,
                "translate_start_ms": 1000,
                "translate_end_ms": 3000,
            },
            {
                "index": 1,
                "chunk_id": "chunk_0001",
                "start_ms": 5000,
                "end_ms": 65000,
                "slice_start_ms": 5000,
                "slice_end_ms": 5100,
                "stt_start_ms": 5100,
                "stt_end_ms": 20000,
                "translate_start_ms": 6000,
                "translate_end_ms": 8000,
            },
        ],
    }


def test_gantt_lines_for_each_chunk():
    lines = analyze_trace.gantt(_perf())
    assert lines[0].startswith("execution timeline")
    assert any(line.startswith("chunk   0") for line in lines)
    assert any(line.startswith("chunk   1") for line in lines)


def test_gantt_empty_trace_returns_placeholder():
    lines = analyze_trace.gantt({"chunks": []})
    assert lines == ["(no measured chunks)"]


def test_stage_windows_are_absolute_min_max():
    w = analyze_trace.stage_windows(_perf())
    assert w["slice"] == {"start_ms": 0, "end_ms": 5100}
    assert w["stt"] == {"start_ms": 100, "end_ms": 20000}
    assert w["translate"] == {"start_ms": 1000, "end_ms": 8000}
    # tts never ran -> not keyed
    assert "tts" not in w


def test_overlap_report_counts_pipeline_parallelism():
    o = analyze_trace.overlap_report(_perf())
    assert o["wall_ms"] == 100000
    assert o["serial_index_ms"] == 705000
    assert o["overlap_factor"] == 7.05  # 7x+ faster than a serialized run


def test_concurrency_audit_uses_pool_from_config():
    c = analyze_trace.concurrency_audit(_perf())
    assert c["stt"]["pool"] == 4
    assert c["stt"]["peak_active"] == 4
    assert c["translate"]["queue_total_ms"] == 5000


def test_resources_per_stage_attribute_to_active_windows():
    trace_peaks = {
        "timeline": [
            {"t_rel_s": 0.0, "cpu_percent": 10.0, "rss_mb": 100.0},
            {"t_rel_s": 5.0, "cpu_percent": 200.0, "rss_mb": 500.0},
            {"t_rel_s": 30.0, "cpu_percent": 300.0, "rss_mb": 700.0},
        ]
    }
    w = analyze_trace.stage_windows(_perf())
    r = analyze_trace.resources_per_stage(trace_peaks, w)
    # t=5s falls in slice [0,5.1), stt [0.1,20) and translate [1,8)
    assert r["stt"]["cpu_peak_percent"] == 200.0
    assert r["translate"]["cpu_peak_percent"] == 200.0
    # t=30s falls outside every stage window
    assert r["slice"]["cpu_peak_percent"] == 200.0
    assert r["slice"]["ram_peak_mb"] == 500.0


def test_summarize_assembles_all_sections():
    s = analyze_trace.summarize(_perf())
    assert s["gantt"]
    assert s["windows_ms"]["stt"]
    assert s["overlap"]["overlap_factor"] > 0
    assert s["concurrency"]["stt"]["pool"] == 4