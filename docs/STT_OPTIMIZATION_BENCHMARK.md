# STT OPTIMIZATION BENCHMARK — Phase 2 (chunk/workers/device + internal audit)

Ngày: 2026-08-19 · Máy dev: i7-10850H 6C/12T, 32 GB RAM, Quadro T1000 4 GB (driver 582.16) · faster-whisper 1.2.1 · model `small` · `device=cuda` (`int8_float16`).
Mọi con số = đo thật bằng `worker/tests/integration/bench_stt.py` (chạy pipeline thật qua loopback HTTP với provider mock, identity check PASS, `MetricSampler` cho GPU/VRAM/CPU peaks). **Mọi cấu hình đều PASS segment identity (transcript ids == translation ids, 1:1 đúng thứ tự), 0 retry, 0 failed chunk.**

## 1. Audit internal pipeline (mode `internal`)

Đo 60 s wav thật (edge-tts) với 1 lần `transcribe` chuẩn trên GPU:

| Mục | Giá trị |
|---|---|
| Model reuse | **PASS** — `_load_whisper_model` trả về cùng instance (cache key `('small','cuda','int8_float16',None)`), cache size = 1 |
| `transcribe` wall | 7.36 s → RTF **0.123** |
| `encode` tổng (sum) | 2.115 s → **28.7 %** wall (3 calls, `[80×3000]`) |
| Decode + token gen + VAD | ~5.2 s → **~71 %** wall |
| Encode đầu tiên | 1.21 s (init layer + CPU→GPU copy), trung bình 0.71 s/call |

Kết luận: **encoder không phải chỗ nặng nhất; vòng decode/token-generation chiếm ~71 %** → cách tăng throughput nhanh nhất là (a) giảm decode qua `beam_size` hoặc (b) chạy song song nhiều chunk (`stt_workers`) để lấp GPU trong lúc decode của chunk này chạy, hoặc (c) `BatchedInferencePipeline` (xem §4).

## 2. Matrix chunk_duration × stt_workers (300 s, GPU, model `small`)

Warm model qua 1 run cd30-w2 trước matrix (model load không tính vào số đo nào). `rtf_pipeline = wall_s / 300`. Lưu ý: `stt_total_ms` là **tổng** stage-time cộng dồn theo chunk → khi workers tăng thì tổng tăng dù wall giảm; số đáng tin là **wall_s / rtf_pipeline**.

| chunk (s) | workers | wall (s) | rtf_pipeline | STT sum (ms) | avg_active | VRAM (MB) | GPU % | Identity |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 15 | 1 | 44.04 | 0.147 | 41 138 | 0.97 | 810 | 99 | PASS |
| 15 | 2 | 41.22 | 0.137 | 77 045 | 1.92 | 842 | 99 | PASS |
| 15 | 3 | 41.14 | 0.137 | 114 687 | 2.83 | 1170 | 99 | PASS |
| 15 | 4 | 42.63 | 0.142 | 154 875 | 3.73 | 1498 | 99 | PASS |
| 30 | 1 | 39.32 | 0.131 | 37 799 | 0.98 | 1466 | 99 | PASS |
| 30 | 2 | 37.91 | 0.126 | 71 957 | 1.96 | 1498 | 99 | PASS |
| 30 | 3 | 37.78 | 0.126 | 100 457 | 2.77 | 1498 | 99 | PASS |
| 30 | 4 | 37.88 | 0.126 | 131 049 | 3.55 | 1498 | 99 | PASS |
| 45 | 1 | 45.73 | 0.152 | 43 353 | 0.98 | 1498 | 99 | PASS |
| 45 | 2 | **33.08** | **0.110** | 64 746 | 1.97 | 1530 | 99 | PASS |
| 45 | 3 | 34.70 | 0.116 | 89 639 | 2.70 | 1530 | 99 | PASS |
| 45 | 4 | 34.69 | 0.116 | 121 077 | 3.61 | 1530 | 99 | PASS |
| 60 | 1 | 34.66 | 0.116 | 33 330 | 0.99 | 1498 | 99 | PASS |
| 60 | 2 | **33.11** | **0.110** | 58 427 | 1.80 | 1562 | 99 | PASS |
| 60 | 3 | 33.09 | 0.110 | 84 163 | 2.59 | 1498 | 99 | PASS |
| 60 | 4 | 34.80 | 0.116 | 113 752 | 3.35 | 1530 | 99 | PASS |

- **Tốt nhất: chunk 45–60 s × workers 2** → wall ~33 s, **RTF 0.110** (tốt hơn default 30 s × 4 = 37.9 s / RTF 0.126 khoảng **13 %**).
- Workers > 2 **không giúp**: GPU đã 99 %, thêm chunk chỉ tăng tranh chấp (avg_queue tăng, CPU peak thêm). Với workers=2, CPU core dành cho decode vẫn đủ (thread budget 12//2).
- VRAM peak cao nhất **1562 MB / 4096 MB** — an toàn lớn, không kích hoạt VRAM guard.
- Chunk 15 s tệ nhất (20 chunk → nhiễu overlap + đầu việc), kể cả ở workers 1 (44.0 s).

## 3. So với baseline

| | Baseline (pha 1, GPU, 30 s × w4) | Best (pha 2, 60 s × w2) | Δ |
|---|---|---|---|
| Chunked wall (s) | 37.9 | **33.1** | **−13 %** |
| Pipeline RTF | 0.126 | **0.110** | −0.016 |
| STT RTF (wall-basis) | 0.126 | **0.110** | −0.016 |
| VRAM peak (MB) | 1498 | 1562 | +64 |

Trước đó pha 1 đo GPU 300 s ≈ 46.19 s (RTF 0.154) — con số này bao gồm model load lạnh trong lần đo gộp; matrix pha 2 đo warm-model nên ráp hơn và nhất quán giữa 16 cấu hình (fair để so workers/chunk).

## 4. Batch (BatchedInferencePipeline) — đo, chưa áp dụng

**Đo thật 60 s wav, GPU:** single `transcribe` 7.36 s (RTF 0.123) so với `BatchedInferencePipeline` (batch_size=8): **3.72 s (RTF 0.062)** — nhanh **~1.98×**; với `word_timestamps=True` 4.23 s (RTF 0.071).

**Vì sao chưa áp dụng:** batched pipeline trả segment thô hơn (2 segment 30 s thay vì 15 segment/câu cho 60 s như decode thường) → timeline/subtitle mất độ chi tiết, cần bước dựng lại timestamp mới đạt schema. Đó là **thay đổi kiến trúc STT stage**, ngoài phạm vi Phase 2 ("chỉ benchmark + chọn config, giữ pipeline"). Ghi nhận là hướng **tiếp theo** để vượt stretch target 0.083 (con số tham chiếu: ≥0.062 khi warm + batch).

## 5. Targets STT

| Target | RTF | Đạt? |
|---|---|---|
| Target 1 | < 0.15 | ✅ (0.110, mọi config chunk ≥30 pha này) |
| Target 2 | < 0.12 | ✅ **0.110** (chunk 45–60 s × w2) |
| Stretch | ≈ 0.083 | ⚠️ chỉ khi dùng batch stage (0.062–0.071) — ngoài kiến trúc hiện tại |

## 6. Khuyến nghị

1. **Run chunked với `chunk_duration=60` và `stt_workers=2`** khi có GPU (RTF 0.110, VRAM 1.5/4 GB, identity PASS). Không cần chạm code: cả hai là tham số request đã tồn tại (`/v1/automation/chunked`).
2. Giữ `stt_device=auto` (resolution → cuda khi có GPU). Không tăng workers trên 2 khi GPU lúc nào cũng 99 %.
3. Batch stage (BatchedInferencePipeline) = hướng tiếp theo duy nhất để hạ thêm ~45 %, nhưng cần approval vì đổi kiến trúc STT (segment granularity + timestamp reconstruction).
4. Model `small` đủ nhanh trên GPU này; `turbo`/`large-v3` (VRAM 2.5–2.9 GB) vẫn vừa 4 GB nhưng RTF chưa đo ở máy này — không khuyến nghị nếu không muốn tăng latency.

## Evidence

- Driver: `worker/tests/integration/bench_stt.py` (mode `internal` + `matrix`, chạy pipeline thật qua HTTP, identity check + MetricSampler; mọi số in ra đều đo thật).
- Raw: `tc_bench_stt_mtx_*/bench_stt_matrix.json` (16 runs, identity PASS toàn bộ).