# PERFORMANCE_TRACE

## Baseline — coupled pipeline (pre-streaming, 300s)

- Job: `e2e-chunked`
- Config: 10 chunks | 30s chunk | 2s overlap | max_concurrency=4 | max_retries=2
- Pipeline wall time: **97.56s** (source video 300s -> **3.1x realtime**)
- Chunk-level concurrency: peak 4/4, avg 3.67/4
- Rendered: 2026-08-18T07:17:10+00:00Z

## Stage utilization

| stage | total | chunks_ran | peak_active | avg_active | vs max_workers |
|---|---|---:|---:|---:|---:|
| slice | 911ms | 10 | 4 | 0.01 | 0% |
| stt | 235.76s | 10 | 4 | 2.42 | 60% |
| translate | 11ms | 10 | 1 | 0.00 | 0% |
| tts | 121.24s | 10 | 4 | 1.24 | 31% |

A stage with `avg_active` near `max_concurrency` is the pipeline's parallel hot loop; a stage with `avg_active << 1` was nearly idle in wall-clock terms.

## Slowest chunks

### stt

| chunk | start_ms | stage | wall |
|---|---:|---:|---:|
| chunk_0002 | 0 | 33.45s | 41.76s |
| chunk_0003 | 0 | 31.86s | 41.06s |
| chunk_0004 | 1 | 30.36s | 59.44s |
| chunk_0001 | 0 | 28.91s | 38.45s |
| chunk_0007 | 41755 | 25.33s | 39.97s |
| chunk_0006 | 41062 | 24.72s | 40.43s |
| chunk_0005 | 38447 | 19.89s | 28.74s |
| chunk_0008 | 59446 | 19.00s | 27.72s |

### translate

| chunk | start_ms | stage | wall |
|---|---:|---:|---:|
| chunk_0001 | 0 | 5ms | 38.45s |
| chunk_0002 | 0 | 1ms | 41.76s |
| chunk_0003 | 0 | 1ms | 41.06s |
| chunk_0004 | 1 | 1ms | 59.44s |
| chunk_0006 | 41062 | 1ms | 40.43s |
| chunk_0007 | 41755 | 1ms | 39.97s |
| chunk_0009 | 67189 | 1ms | 24.29s |

### tts

| chunk | start_ms | stage | wall |
|---|---:|---:|---:|
| chunk_0004 | 1 | 29.01s | 59.44s |
| chunk_0006 | 41062 | 15.62s | 40.43s |
| chunk_0007 | 41755 | 14.53s | 39.97s |
| chunk_0009 | 67189 | 9.60s | 24.29s |
| chunk_0001 | 0 | 9.44s | 38.45s |
| chunk_0003 | 0 | 9.13s | 41.06s |
| chunk_0005 | 38447 | 8.78s | 28.74s |
| chunk_0008 | 59446 | 8.60s | 27.72s |

## Full per-chunk trace

| chunk | index | start_ms | end_ms | slice | stt | translate | tts |
|---|---:|---:|---:|---:|---:|---:|---:|
| chunk_0001 | 1 | 0 | 38446 | 90ms | 28.91s | 5ms | 9.44s |
| chunk_0002 | 2 | 0 | 41755 | 90ms | 33.45s | 1ms | 8.21s |
| chunk_0003 | 3 | 0 | 41062 | 70ms | 31.86s | 1ms | 9.13s |
| chunk_0004 | 4 | 1 | 59445 | 69ms | 30.36s | 1ms | 29.01s |
| chunk_0005 | 5 | 38447 | 67188 | 68ms | 19.89s | 0ms | 8.78s |
| chunk_0006 | 6 | 41062 | 81489 | 90ms | 24.72s | 1ms | 15.62s |
| chunk_0007 | 7 | 41755 | 81723 | 111ms | 25.33s | 1ms | 14.53s |
| chunk_0008 | 8 | 59446 | 87170 | 116ms | 19.00s | 0ms | 8.60s |
| chunk_0009 | 9 | 67189 | 91483 | 92ms | 14.60s | 1ms | 9.60s |
| chunk_0010 | 10 | 81490 | 97557 | 113ms | 7.64s | 0ms | 8.31s |

## Streaming pipeline (2026-08-18) — production path hiện tại

Stage-decoupled bounded pools (`StreamingChunkPipeline`): STT→translate→TTS
chạy pool riêng, chunk xong STT nhường slot ngay. So sánh cùng fixture 300s,
cùng máy, cùng STT `small` CPU:

| Metric | Coupled (baseline) | Streaming | Delta |
|---|---|---:|---:|
| Pipeline wall | 97.56s | **75.49s** | **−22.6%** |
| Realtime (300s source) | 3.1x | **3.97x** | +0.87x |
| STT pool utilization | 2.42/4 (60%) | **3.16/4 (79%)** | +19pp |
| TTS pool utilization | 1.24/4 (31%) | 1.51/4 (38%) | +7pp |

Ladder 60s→40min (driver `e2e_chunked.py`, STT thật + mock translate + edge TTS dub + render/finalize thật):

| Rung | Source | Chunks | Chunked wall | Realtime | STT util | Identity | Finalize |
|---|---|---:|---:|---:|---:|---|---|
| 60s no-dub | 60s | 2/2 | 19.67s | 3.05x | — | 16 seg | PASS |
| 60s dub | 60s | 2/2 | 27.12s | 2.21x | — | 16 seg | PASS |
| 5 min dub | 300s | 10/10 | 75.62s | 3.97x | 3.16/4 | 86 seg | PASS |
| 10 min dub | 600s | 20/20 | 238.11s | 2.52x | 2.34/4 | 157 seg | PASS |
| **40 min dub** | 2400s | **80/80** | **660.03s** | **3.64x** | **3.64/4 (91%)** | **615 seg** | **PASS** |

40-min full trace (`%TEMP%\tc_e2e_chunk_ay7aj3wx\project\cache\performance_trace_e2e-chunked.json`):

- `wall_elapsed_s` 659.49 · chunk-level peak 16/4, avg 7.05/4 (các stage overlap nhau)
- slice total 7.3s peak 4 · **stt total 2403.1s peak 4 avg 3.64/4** · translate total 44ms
  · tts total 1637.1s peak 4 avg 2.48/4
- Output 2400.0s (310.9 MB, h264/aac), 615 segment identity PASS, cleanup done.
