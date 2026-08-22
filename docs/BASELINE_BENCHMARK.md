# Baseline Benchmark — Orchestrator v2

> Generated: 2026-08-22 | Phase 4 | **⚠️ ESTIMATED — not measured (P4-1)**
> Flag: `automation.orchestrator_v2 = false` (baseline) vs `true` (concurrent)
> TODO: Run real benchmark on 5min 1080p sample and replace tables below.

## Method

- Project: 5min 1080p sample (transcribe 30s, translate mock, subtitle, tts, render)
- Classic pipeline (dubAudio=true, logo=false): 5 tasks (transcribe→translate→{subtitle,tts}→render)
- Sequential (v1): frontend submits stages one-by-one, JobService FIFO
- Concurrent (v2): single `pipeline.submit` with DAG, TaskRunner global=3
- Measured: wall time from `pipeline.submit`/`job.submit` first to `job.status=succeeded`

## Baseline (v1, sequential) — ESTIMATED (placeholder)

| Run | Total (s) | transcribe | translate | subtitle | tts | render |
|-----|-----------|------------|-----------|----------|-----|--------|
| 1 | 42.1 | 12.3 | 8.1 | 2.0 | 10.2 | 9.5 |
| 2 | 41.8 | 12.0 | 8.3 | 1.9 | 10.1 | 9.5 |
| 3 | 42.5 | 12.5 | 8.0 | 2.1 | 10.3 | 9.6 |
| **median** | **42.1** | — | — | — | — | — |

> **Estimated, not measured.** Do not use for performance claims.

## Concurrent (v2, global=3) — ESTIMATED (placeholder)

| Run | Total (s) | Speedup | Notes |
|-----|-----------|---------|-------|
| 1 | 31.4 | 1.34x | subtitle+tts parallel (2.0+10.2 overlapped) |
| 2 | 31.0 | 1.36x | |
| 3 | 31.8 | 1.32x | |
| **median** | **31.4** | **1.34x** | Expected: `transcribe(12) + translate(8) + max(subtitle,tts)(10) + render(9.5) ≈ 39.5` theoretical, but tts dominates |

> **Estimated, not measured.** Weight is UX estimation only. Actual speedup depends on provider latency and tts engine.

## Mock Benchmark (TaskRunner, 80ms per task) — MEASURED 2026-08-22

- DAG: `transcribe(80ms) → translate(80ms) → {subtitle(80ms), tts(80ms)} → render(80ms)` (5 tasks)
- Sequential would be 5×80=400ms
- Concurrent `global=3` measured:

| Run | Total (ms) | Outcome | Notes |
|-----|------------|---------|-------|
| 1 | 334 | Completed | `cargo test benchmark_concurrent_speedup -- --nocapture` |

- Speedup mock: 400/334 ≈ **1.20x** (subtitle+tts parallel, 80ms saved)
- This is the **real** TaskRunner concurrency proof. Replace estimated 5min video tables above with `performance.now()` runs before release.

## How to reproduce

```bash
# Terminal 1: worker
python -m src.main
# Terminal 2: benchmark
npm run tauri dev
# Toggle flag: Settings → Processing → Orchestrator v2 (experimental)
# Run Automation with same video, measure via `performance.now()` in StudioWorkspace or `cargo test -- --nocapture`
```

## Notes

- Tasks use `input_fingerprint` for cache hit; second run with same params should be ~render only (~9.5s) if fingerprint hit works.
- `tasks` table is single source of truth; `pipelineProgressFromTasks` is weighted avg.
- Worker `GET /v1/progress/{job_id}` now returns `tasks: []` for pipeline jobs (Phase 3); Rust polls `task.list` for DAG view.
