# Performance Optimization - Automation Pipeline

Ngày bắt đầu: 2026-08-15

## Mục tiêu

Tối ưu pipeline Automation để xử lý video dài nhanh hơn mà không giảm chất lượng, không redesign UI, không mở rộng scope MVP, và không thay đổi provider system nếu không cần.

Ưu tiên: Correctness > Stability > Performance > UI.

## Phạm vi task

- Đo thời gian từng stage pipeline thay vì đoán bottleneck.
- Tạo performance report có stage timing, tổng thời gian, segment count và thông tin tài nguyên nếu thu thập được.
- Audit audio/video pipeline để tránh extract/encode lặp lại.
- Tối ưu batching/concurrency/cache/incremental processing trong phạm vi architecture hiện tại.
- Giữ mọi thay đổi có kiểm chứng bằng test/smoke check.

## Checklist Definition of Done

- [x] Có profiling từng stage (perf trace: slice/stt/translate/tts + render + finalize).
- [x] Xác định top 3 bottleneck bằng số đo.
- [ ] Translation batching hoạt động nếu provider hỗ trợ (LLM thật không có trên máy — đã đo overhead stub HTTP).
- [x] TTS bounded concurrency hoạt động (dub edge 10 chunk: peak 4/avg 2.18, queue bounded).
- [x] Không encode video dư thừa (extract 1 lần; render 1 lần).
- [x] Audio extraction không lặp lại.
- [x] Cache hoạt động với key phụ thuộc input/config phù hợp.
- [x] Incremental processing hoạt động (chunked streaming + ordered assembly).
- [x] Memory không tăng bất thường (RAM peak ~0.7 GB).
- [x] GPU được dùng khi phù hợp (STT batched CUDA; nvenc render bị chặn bởi driver cũ).
- [x] CPU fallback hoạt động (stt_device=cpu; render fallback libx264).
- [x] Existing Automation không regression (E2E full chain PASS chunk 30 + chunk 120 batched có guard; identity 1:1).
- [x] Existing tests pass (158 unit/integration + 7 analyze_trace; thêm 5 test P1 guard).

## Tiến độ

### 2026-08-15

- [x] Đọc yêu cầu task từ attachment.
- [x] Tạo file tiến độ riêng: `PERFORMANCE_AUTOMATION_PROGRESS.md`.

### 2026-08-20 (benchmark đầy đủ — đo thật, không bịa số)

- [x] Cải tiến perf trace: per-stage absolute start/end trong từng chunk row + expose `config`/`chunks` trong manifest `perf`.
- [x] Driver `worker/tests/integration/bench_stt.py`: STT-stage bench (model×device×workers×batch×chunk) qua chunked endpoint.
- [x] Module `worker/tests/integration/analyze_trace.py` + unit test `test_analyze_trace.py`: Gantt execution timeline, stage windows, overlap factor, concurrency audit, per-stage resource attribution.
- [x] `e2e_chunked.py` ghi `report.json` trong EVIDENCE_DIR; `MetricSampler` lưu sample timeline (`t_rel_s`) cho per-stage attribution.
- [x] TEST A/B/C/D/E: full pipeline 60s (CPU) / 300s×GPU mock + local + dub / **60 phút (GPU)** — validation PASS mọi run.
- [x] Chẩn đoán render: nvenc không mở được vì ffmpeg 9.0 cần nvenc API ≥13.1 (driver ≥610), máy có driver 582.16 → fallback libx264 tự động hoạt động đúng.
- [x] Đo STT ma trận: GPU batched `small` w2 b4 = RTF 0.099 (300s); CPU best 0.265; chunk 30→120s giảm RTF 0.099→0.066.
- [x] 60 phút full pipeline (GPU, small, chunk 30): **~9.8 phút** (RTF 0.164), STT chiếm 355.9s (~6 phút).

### 2026-08-20 (bổ sung — long-chunk matrix + P1 STT quality guard)

- [x] Mở rộng `bench_stt.py`: flag `--chunk-durations` (multi-pass 1 lần gọi), `--lang en|vi`, MetricSampler mỗi config, lưu `bench_report.json`.
- [x] Module `measure_quality.py` (`worker/tests/integration`): đọc `transcript.json` mỗi run → coverage, monotonic, max-interior-gap, chunk-order, chunk-local-spread, out-of-span.
- [x] Long-chunk matrix (300s, GPU `small` batched w2 b2): c180 RTF **0.070** (nhanh nhất), c90/c180 quality khỏe (coverage 0.750, gap ~1.2s). **Phát hiện bug:** c60/c120 mất block **21.3s** speech tại cùng vị trí (window bắt đầu 118s) — reproduce deterministic 2 lần.
- [x] Root-cause (đo thật): early-EOS của faster-whisper `BatchedInferencePipeline` (decode batch đầu cửa sổ giữa câu sinh 8 token rồi `<|endoftext|>`, nuốt ~20s lời); output vẫn hợp lệ hình thức nên validator cũ không bắt. VAD silero đủ; `regular` phủ 100%; dịch start +0.25s hết vỡ.
- [x] **P1 implemented**: guard STT quality trong `chunk_service._run_stt_stage` — `_large_interior_gaps` + `_segment_gap_has_speech` (energy qua `wave`/`array`, không numpy/audioop) → khi batched có interior gap >3s vùng có speech thì re-run chunk đó `stt_mode="regular"`. Cost không miss = 0 (no audio I/O); cost miss ≈ +13s/chunk.
- [x] Verified P1: 5 unit test mới (`TestSttQualityGuard`), full worker suite **158 passed**, smoke import OK; E2E re-chạy fixture từng vỡ → **guard fire, gap 21.28→1.29s, coverage 0.69→0.75**.
- [x] E2E full chain regression (chunk 120, batched, guard active): **VALIDATION PASS** chunks 3/3, segments 88, retries 0; guard fire trên chunk_0002 trong pipeline thật (chạm đúng bug cũ); tổng wall 80s / 300s fixture.
- [x] Ghi report: `docs/BENCHMARK_REPORT_2026-08-20.md` §8 (long-chunk + quality metrics) và §9 (P1).

## Ghi chú kỹ thuật

- Repo đang có nhiều thay đổi sẵn trước task này; chỉ chỉnh trong phạm vi cần thiết.
- Baseline performance đã xác lập 2026-08-20 (xem `docs/BENCHMARK_REPORT_2026-08-20.md`).

## Performance Report

Mọi số liệu đo thật ngày 2026-08-20, machine i7-10850H 6C/12T · 32 GB RAM · Quadro T1000 4 GB · driver 582.16. Fixture: speech edge-tts/SAPI + testsrc2. STT model `small` (trừ cột model ghi riêng).

| Video length                   | Total time  | STT (chunked)           | Translation     | TTS                  | Encoding        | RAM peak | VRAM peak | GPU   | Speedup vs realtime | Ghi chú        |
| ------------------------------ | ----------- | ----------------------- | --------------- | -------------------- | --------------- | -------- | --------- | ----- | ------------------- | -------------- |
| 60 s (CPU, w4)                 | 27.5 s      | 17.6 s (RTF 0.275)      | mock ~0         | —                    | libx264         | n/a      | n/a       | —     | 2.2×                | TEST A         |
| 300 s (GPU, w4 b2, mock)       | 62.0 s      | 32.4 s (RTF 0.108)      | mock            | —                    | libx264 9.0 s   | 680 MB   | 2660 MB   | 100 % | 4.8×                | TEST B         |
| 300 s (GPU, w4 b2, local/stub) | 54.4 s      | 29.5 s (RTF 0.098)      | stub 175 ms tot | —                    | libx264 10.0 s  | n/a      | 2660 MB   | 100 % | 5.5×                | TEST C         |
| 300 s (GPU, w4 b2, dub edge)   | 76.0 s      | 42.4 s (RTF 0.141)      | mock            | 91.9 s tot (overlap) | libx264 13.8 s  | n/a      | n/a       | 100 % | 3.9×                | TEST D         |
| **3600 s (GPU, w2 b4, mock)**  | **590.7 s** | **355.9 s (RTF 0.099)** | mock ~0         | —                    | libx264 110.5 s | 704 MB   | 2628 MB   | 100 % | **6.1×**            | TEST E 60 phút |
