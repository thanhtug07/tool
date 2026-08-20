# BENCHMARK_REPORT 2026-08-20 — Toàn bộ số đo thật trên máy dev

Ngày: 2026-08-20 · Môi trường: máy dev (local-development-only). Tất cả con số dưới đây đều được **đo thật** bằng `e2e_chunked.py` (full chain extract → chunked → render → QC → finalize) và `bench_stt.py` (STT-stage trên chunked endpoint, renderless) với fixture real (edge-tts/SAPI speech + testsrc2 @ ffmpeg). Không có số liệu bịa/ước lượng suông.

## 1. Hardware & phần mềm (đo lại 2026-08-20)

| Hạng mục | Giá trị đo |
|---|---|
| CPU | Intel Core i7-10850H — 6 physical / 12 logical cores |
| RAM | 34.1 GB |
| GPU | NVIDIA Quadro T1000 — 4096 MiB, driver **582.16** |
| ffmpeg | `vendor/ffmpeg` 9.0-essentials (gyan) — yêu cầu nvenc API ≥ 13.1 / driver ≥ 610 |
| Python | 3.13 worker (chốt deploy 3.11) |
| STT | faster-whisper `small` (cached: tiny/small/large-v3/large-v3-turbo) |

> nvenc API: ffmpeg 9.0 báo `Required: 13.1, Found: 13.0` → **h264_nvenc/hevc_nvenc không dùng được trên driver 582**; render_service fallback libx264 tự động (đúng thiết kế, warning trong log).

## 2. STT-stage benchmark (chunked endpoint, 300 s audio, mock translate)

| config (device · mode · w · batch · chunk) | wall (s) | RTF | ghi chú |
|---|---|---|---|
| CPU · regular · w1 · (chunk 30) | 88.4 | 0.295 | serial |
| CPU · batched · w4 · b2 · (30) | 84.4 | 0.281 | pool đầy nhưng cạnh tranh core |
| CPU · batched · w2 · b4 · (30) | 79.6 | 0.265 | batch 4 giúp CPU count nhanh hơn |
| **GPU(auto) · batched · w2 · b4 · (30)** | **29.6** | **0.099** | **tốt nhất model small** |
| GPU(auto) · batched · w4 · b2 · (30) | 29.9 | 0.100 | 4 worker tương đương w2 |
| GPU(auto) · batched · w1 · b4 · (30) | 34.0 | 0.113 | pool 1 có lúc đói |
| GPU · batched · w2 · b4 · **chunk 60** | 23.9 | **0.080** | ít scheduler overhead |
| GPU · batched · w2 · b4 · **chunk 120** | 19.9 | **0.066** | nhanh nhất đo được (STT-only) |
| GPU · large-v3-turbo · w2 · b4 | 55.7 | 0.186 | Turbo chậm hơn small trên T1000 |
| GPU · large-v3-turbo · w4 · b2 | 183.7 | 0.612 | VRAM swap nặng |
| GPU · large-v3 · w1 · b2 | 295.6 | 0.985 | default production → ~1× realtime |
| GPU · large-v3 · w2 · b4 | 527.8 | 1.759 | swap nghiêm trọng |

Kết luận STT:
- **GPU batched `small` là thông số nhanh nhất **trên card 4 GB này**; các model lớn hơn (turbo/large-v3) swap VRAM và chậm hơn rõ rệt.
- ⚠️ Default production `stt_model="large-v3"` (chunk_service.py) với VRAM guard (4096 ≥ 2900 → giữ nguyên) cho RTF ~1 → **60 phút ≈ 59 phút STT**. Đây là bottleneck lớn nhất nếu config production không đổi.
- `stt_workers` trên GPU: tăng 1 → 2 lời ~12 %, 2 → 4 gần như 0 — tài nguyên hạn chế là GPU, không phải số engine.
- Chunk duration: 30 → 60 → 120 s giảm RTF 0.099 → 0.080 → 0.066 (STT-only).*Chưa áp dụng vào config, vì thay đổi tham số chunk/overlap cần chạy full-E2E xác nhận timeline + quality.*

## 3. Full pipeline (e2e_chunked, validation QC pass mọi run)

| Test | duration | config | extract | chunked (RTF) | render | finalize | total (s) | RAM peak | VRAM peak | GPU util |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 60 s | CPU small w4 b2 | 0.3 | 17.6 (0.275) | ~2 | 1.1 | 27.5 | — | — | — |
| B | 300 s | GPU small w4 b2 mock | 0.34 | 32.4 (0.108) | 9.0 | 1.1 | 62.0 | 680 MB | 2660 MB | 100% |
| C | 300 s | GPU small w4 b2 **local(stub)** | 0.3 | 29.5 (0.098) | 10.0 | 1.1 | 54.4 | — | 2660 MB | 100% |
| D | 300 s | GPU small w4 b2 **dub edge** | 0.3 | 42.4 (0.141) | 13.8 (voice mix) | 1.1 | 76.0 | — | — | 100% |
| **E** | **3600 s** | **GPU small w2 b4 mock** | **2.4** | **355.9 (0.099)** | **110.5** | **1.2** | **590.7** | **704 MB** | **2628 MB** | **100%** |

- TEST E (60 phút): **120/120 chunks, 949 segments, retries=0, identity 1:1, ffprobe QC + luma 124.07 + max_vol -9.0 dB pass**.
- `total` bao gồm fixture build (60 phút: 113.6 s) — pipeline-only wall thấp hơn nữa.
- Speedup so với realtime: 60 phút video → **6.1×** (nếu trừ fixture build: 3600/477 ≈ 7.5×).

### TTS stage (TEST D — dub edge tiếng Việt, 76 segments)
- TTS stage total 91.9 s trên 10 chunk, `peak_active 4 / avg 2.18`, queue 4.7 s → **overlap song song với STT** tốt: chunked chỉ +13 s so với run không dub.
- Overlap factor toàn pipeline TEST D: **5.02** (serial 211.7 s ⇒ wall 42.4 s).

### Render
- Không codec phần cứng khả dụng: nvenc API không đủ (driver 582 < 610). libx264 veryfast: 300 s ≈ 9–14 s, 3600 s ≈ 110 s (không phải bottleneck).
- Nâng cấp driver ≥ 610 (hoặc dùng máy khác) mới thử được nvenc — cần benchmark lại trước khi bật; cho tới lúc đó giữ fallback.

## 4. Bottleneck xếp hạng (có số đo)

1. **STT** = > 95 % thời gian chunked (TEST E: stt total 700 s dàn trên 355.9 s wall). Duy nhất tài nguyên nghẽn là GPU (util 100 % xen kẽ).
2. **Render** = 110.5 s / 590.7 s (19 %) — đáng kể nhưng chỉ khi STT đã được tối ưu.
3. **Translate / slice / finalize / extract** < 1 % (stub path thật tổng 175 ms/10 chunk).

## 5. Khuyến nghị (cần approval trước khi đổi mặc định — không tự đổi)

1. **Đổi default STT model `large-v3` → `small`** khi chạy video dài trên máy ≥1 GPU 4 GB: 60 phút STT giảm 59 → ~6 phút (RTF 0.985 → 0.099). Trade-off chất lượng từng đoạn WER chưa đo trên corpus tiếng Việt — cần approval (AGENTS.md cứng: model default không tự đổi).
2. **`stt_device=auto` mặc định**: máy có CUDA nên batched CUDA (đã là hành vi auto).
3. **Chunk duration ≥ 60 s trên GPU** giảm tiếp RTF ~20–33 % (đo STT-only; cần full-E2E + timeline qual trước khi áp).
4. **Cập nhật driver NVIDIA ≥ 610** để mở khóa nvenc — render có thể giảm từ ~11 % xuống ~1–2 %; benchmark lại sau.
5. Pipeline hiện tại đã overlap tốt (overlap factor 5.02), không cần đổi architecture.

## 6. Cải tiến observability (đã commit trong nhánh này)

- `chunk_service.build_performance_trace`: mỗi chunk row chứa thêm `{stage}_start_ms/_end_ms` (absolute) cho execution timeline.
- Manifest `perf` (HTTP + disk trace) giờ gồm `config` + `chunks`.
- `worker/tests/integration/analyze_trace.py` (+ unit tests): Gantt ASCII, stage windows, overlap factor, concurrency audit, per-stage resource attribution.
- `bench_stt.py`: driver STT-stage matrix. `MetricSampler` lưu sample timeline `t_rel_s`.
- `e2e_chunked.py` ghi `report.json` kèm `trace_analysis` trong EVIDENCE_DIR.

## 7. Files & evidence

- EVIDENCE_DIR TEST E: `%TEMP%\tc_e2e_chunk_*` (mới nhất) — `report.json`, `performance_trace_e2e-chunked.json`, `chunk_manifest_e2e-chunked.json`, `rendered.mp4`.
- Benchmark STT: `%TEMP%\tc_bench_stt_*`.
- Ngoài phạm vi vẫn còn (không có tài nguyên/mạng): LLM translate thật (Gemini/llama) — chỉ đo được overhead stub 175 ms/10 chunk; edge-tts latency thật đã đo trong TEST D (TTS overlap).

## 8. Long-chunk matrix (chunk 60–180 s) + transcript quality metrics (2026-08-20)

Bổ sung vào §2: bench STT-only đã chỉ đo RTF; lần này thêm `measure_quality.py` đọc trực tiếp `transcript.json` mỗi run (coverage, monotonic, gap, chunk-order, spread). Fixture 300 s (speech edge-tts liên tục), GPU `small` batched w2 b2, 2× repeat cho repro:

| chunk (s) | wall (s) | RTF | segs | coverage | max interior gap (s) | chunk_order | spread > expect |
|---|---:|---:|---:|---:|---:|---:|---|
| 60 | 24.92 | 0.083 | 72 | 0.6938 | **21.28** | ✓ | ✗ |
| 90 | 24.77 | 0.083 | 78 | 0.7496 | 1.23 | ✓ | ✗ |
| 120 | 21.41 | 0.071 | 71 | 0.6945 | **21.28** | ✓ | ✗ |
| 180 | 21.08 | 0.070 | 76 | 0.7517 | 1.21 | ✓ | ✗ |

Repro run (lần 2): c120 → 71 segs / coverage 0.6928 / gap **21.28** (giữ nguyên); c90 → 78 segs / 0.7515 / no-gap. Kết luận chắc chắn.

**Phát hiện quality (quan trọng):** cả c60 lẫn c120 đều tái hiện một block **21.3 s bị mất trong transcript** tại cùng vị trí toàn cục [125.5 → 146.8]:
- Block nằm **giữa một chunk** (không phải do merge ranh giới): c60 chunk_0003 audio=[118,182], c120 chunk_0002 audio=[118,242] — audio có speech đều (RMS ≈ 1400 full-track), không phải silence fixture.
- c90 (chunk_0002=[88,182]) và c180 (chunk_0001=[0,182]) **không** mất block → không phải thuộc tính riêng của chunk dài, mà xảy ra khi chunk window bắt đầu đúng tại offset ~118 s (common phrasing: mất ~21 s ở local [7.5, 28.8] của chunk).
- Tác động: coverage giảm 0.750 → 0.693 (~−7,6 %), RTF có thể giảm 0.083 → 0.070 nhưng **chưa nên áp chunk ≥120 s sẵn** vì nguy cơ mất nguyên một khối lời nói trong chunk; cần audit nguyên nhân drift/timeline ở STT stage.

Evidence: `%TEMP%\tc_bench_stt_viq2u00b\` (matrix 4 config), `tc_bench_stt_1xh3e6qj\` (c120 repro), `tc_bench_stt_fuvvj09b\` (c90 repro). Code: `worker/tests/integration/bench_stt.py` (flags `--chunk-durations`, `--lang`) + `worker/tests/integration/measure_quality.py`.

## 9. P1 — STT quality guard (đã implement + test, 2026-08-20)

Root-cause (đo thật): block 21.3s bị mất là **early-EOS của faster-whisper `BatchedInferencePipeline`** (decode batch đầu cửa sổ chỉ sinh 8 token rồi `<|endoftext|>`, nuốt ~20s lời); output vẫn hợp lệ hình thức (monotonic, in-window) nên validator cũ không bắt. Nhạy mốc sample: dịch start +0.25s hết vỡ. VAD silero liệt kê đủ speech trong vùng đó; `regular` mode phủ 100% block.

**Fix (đã áp vào code + unit test):** `chunk_service._run_stt_stage`:
- Helper `_large_interior_gaps` + `_segment_gap_has_speech` (đọc energy qua `wave`/`array`, không numpy/audioop): phát hiện interior gap > 3s trong logical window mà audio vùng đó có speech energy.
- Khi engine thực chạy là `batched` và gap nghi vấn → re-transcribe chunk đó 1 lần `stt_mode="regular"`, dùng kết quả regular; log `[STT-GUARD]`, perf ghi `stt_guard_fallback_s`.
- Constants: `STT_COVERAGE_RETRY_MIN_GAP=3.0`, `RMS_REL=0.25`, `MIN_RMS=200`, `WINDOW_S=0.5` (tune theo benchmark; không hard-code tại call site).

**Verify (đo thật cùng fixture đã từng vỡ):**
- Unit: `tests/unit/test_chunk_service.py::TestSttQualityGuard` — 5 test mới (gap math, speech gap True, silent gap False, no-I/O fast path). Full suite worker: **158 passed**.
- E2E repro c120 (re-dùng fixture vỡ `tc_bench_stt_1xh3e6qj`): guard fires trong worker log, coverage hồi phục, `max_interior_gap_s` 21.28 → **1.29**.
- Chạy lại matrix c120 bình thường (fixture dựng mới): coverage 0.6928 → **0.7509**, segs 71 → 77, gap 21.28 → **1.29** (guard fire).
- Cost khi không miss: `_large_interior_gaps` là thuần segment math (không I/O) → ~0 overhead. Khi miss: 1 lần re-decode regular (~+13s/chunk trên máy này).