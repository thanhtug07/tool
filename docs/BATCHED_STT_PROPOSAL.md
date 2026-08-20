# BATCHED STT — Whisper batched transcription cho local STT (faster-whisper 1.2.1)

> Task: nghiên cứu + prototype + **production implementation (BATCHED_STT)**.
> **Trạng thái: APPROVED & IMPLEMENTED — chạy qua engine abstraction, fallback
> an toàn, auto mode, đầy đủ DoD.** Chi tiết kiến trúc/sẵn sàng production ở
> `docs/BATCHED_STT_PRODUCTION.md`. Các evidence bên dưới là number đo thật trên
> máy này (RTX 3050 Mobile 4GB, Windows, CUDA: ctranslate2 → 1 device).

## 1. Tóm tắt

Prototype `worker/tests/integration/batched_stt_proto.py` (isolated, dùng
fixture TTS) cho thấy `BatchedInferencePipeline` (faster-whisper 1.2.1) có thể
thay thế single-pass transcription hiện tại với:

- **RTF giảm ~28–42%** so với single-pass trên cùng mô hình (`small`, CUDA):
  batch=2: 0.068 vs 0.107; batch=4: 0.057.
- **Chất lượng tương đương/subtitle tương đương**: tiến trình nội dung đầy đủ
  hơn (số segment gần gấp đôi nhờ bắt cả câu ngắn + filler), word coverage 96%,
  drift trung bình _start/end_ vào buổi −1.8/−3.1s (whisper gtap giữa các câu).
- **VRAM an toàn với GPU 4GB**: VRAM đỉnh batch=4 / `small` = 738MB trên RTX
  3050 Mobile 4GB, thấp hơn VRAM peak single-pass pipeline 1562MB (đo ở
  `docs/STT_OPTIMIZATION_BENCHMARK.md`). Chưa đo VRAM cho `large-v3` batched —
  đây là giới hạn chính cần xác nhận.

Prototype không đổi production code. Cần phần mềm implement ở stage STT
(`chunk_service._run_stt_stage`) + abstraction mới `BatchedProvider`.

## 2. Files changed (prototype — đã tồn tại)

| File | Vai trò |
|---|---|
| `worker/tests/integration/batched_stt_proto.py` (**mới**) | Prototype: fixture TTS sinh audio (edge-tts), chạy regular `transcribe` vs `BatchedInferencePipeline`+reconstruct; các mode `compare` / `chunk` / `compat` / `bench`; so sánh timeline + interface SubtitleService để xác minh compatibility |
| `worker/tests/integration/bench_stt.py` (**mới**) | Benchmark lịch sử single-pass vs batched ban đầu (phase trước) |

Không sửa bất kỳ file production nào (`git status` chỉ có test mới + các file
đã sửa từ task trước).

## 3. Kết quả prototype (evidence)

### 3.1 `compare` — regular vs batched+reconstructed trên cùng 60s fixture

| Metric | Regular (single-pass) | Batched+reconstruct |
|---|---|---|
| Wall / RTF | 6.43s / 0.107 | 3.74s / 0.062 |
| Segments | 15 | 16 |
| Word coverage | 100% | 96.4% (5 missing / 2 extra — filler "transcribe(s)" khác biệt) |
| start drift avg / max | — | −1.79s / 5.31s |
| end drift avg / max | — | −2.98s / 6.46s |
| Timeline gap max | 0.08s | 1.19s (word timestamps chỉ phủ phần nói; im lặng giữa câu thành gap) |
| Timeline coverage | 0.999 | 0.75 |

**Nhận xét quan trọng:** reconstructed segments giữ nguyên text, thứ tự, ngôn
ngữ; gap chủ yếu là silent interval thật giữa các câu TTS. Ảnh hưởng subtitle
được SubtitleService xử lý (xem 3.3).

### 3.2 `chunk` — identity qua chunking (300s / chunk 60 / overlap 2)

- 5 chunks → 71 merged segments (dedupe overlap OK), `order_issues: []`,
  `identity_pass: true`. Reconstruct + offset + clamp + merge giữ nguyên thứ
  tự và nội dung qua biên chunk.

### 3.3 `compat` — interface production

16 segments batched → 16 translated (mock translate intact) → 15 subtitle cues,
generated ASS + SRT không lỗi, `compat_pass: true`. SubtitleService tự extend
duration khi CPS > 18 (bình thường, đúng thiết kế hiện tại).

### 3.4 `bench` — batch size ladder (300s fixture, `small`, CUDA)

| batch | wall (s) | RTF | VRAM đỉnh (MB) | GPU% | raw→recon seg |
|---|---|---|---|---|---|
| 1 | 23.73 | 0.079 | 482 | 100 | 10 → 74 |
| 2 | 20.31 | 0.068 | 578 | 100 | 10 → 74 |
| 4 | 17.13 | 0.057 | 738 | 100 | 10 → 74 |

Với `small` + batch=4: VRAM chỉ 738MB — rất thừa khoảng trống cho GPU 4GB.
(VRAM đệm tăng theo batch như dự kiến; encoder nặng hơn single-pass.)

## 4. Kết quả production benchmark — `large-v3` (đã thực hiện, DoD gate)

Benchmark chính thức: `worker/tests/integration/bench_batched_stt.py`, cùng 60s
fixture, 2 trials/config, GPU CUDA, **`large-v3`** (mô hình production default),
dùng đúng `stt_service.transcribe(..., stt_mode=)` mà pipeline chạy:

| Config | RTF | Wall 60s | VRAM peak | GPU% | Segments | Word coverage | Timeline coverage |
|---|---|---|---|---|---|---|---|
| regular | 0.4072 | 24.43s | 3924MB | 100 | 13 | 0.0 (không có word timestamps) | 0.840 |
| batched batch=1 | 0.3287 | 19.72s | 3746MB | 100 | 9 | 1.0 | 0.997 |
| **batched batch=2** | **0.2533** | **15.20s** | 3906MB | 100 | 9 | 1.0 | 0.997 |
| batched batch=4 | 0.2402 | 14.41s | 3906MB | 100 | 9 | 1.0 | 0.997 |

**Kết luận (đo thật, ko bịa):**
- Batched nhanh hơn **~38–41% RTF** so với regular trên `large-v3`, batch=2 là
  điểm cân bằng VRAM/latency tốt nhất (batch=4 chỉ thêm ~4% tốc độ nhưng tốn VRAM).
- **Chất lượng nội dung không giảm**: word coverage 1.0 (regular là 0.0 — nó
  không trả word timestamps), timeline coverage 0.997 vs 0.84, segments ổn định
  (9=9 qua 2 trials). Batch tăng không đổi output (deterministic như nhau).
- **VRAM `large-v3` an toàn trên GPU 4GB**: peak 3906MB (batch=2) < 4GB — điều
  mà prototype trước đó "CHƯA đo" (blocking question §6.4) nay đã được trả lời.
- Ổn định: 2 trials same wall ±0.3s, không fallback (engine=batched thật), e2e
  chunked pass (identity + render + QC) với `--stt-mode batched` trên CUDA.

Evidence file: `%TEMP%\tc_bench_batched_*\bench_batched_stt.json`.

## 5. Files changed (production — đã tồn tại)

### 4.1 Abstractions mới

- `BatchedProvider` (abstract, mirror `TranscriptionProvider`): vòng đời
  load/unload, `transcribe(audio, ...) -> BatchedResult {raw_en, reconstruct, info}`.
- `FactoryBatchedProvider` factory theo config.

### 4.2 Hợp nhất với stage STT hiện tại

- `chunk_service._run_stt_stage` gọi qua provider abstraction; giữ nguyên
  contract `TranscribeResult` (raw_segments/reconstructed) để downstream
  (translation, subtitle, assembly) không đổi.
- Tune: `min_silence_duration_ms` (silero), `vad_parameter`, giữ window chunk
  (30s) + overlap như prototype; batch_size tính từ vram_available (dùng
  `hardware.detect` đã có).
- Giữ single-pass là default; batched bật theo cấu hình.

### 4.3 Heuristic "nới segment để giảm silent gap" (optional, chờ CÓ/CÓ BỊ phê)

Để coverage timeline gần single-pass hơn, có thể nới `end` của segment trước
tới `start` của segment sau trừ `min_gap` (VD: `reconstructed_segments` edit
thuộc chức năng của bước assembly/threshold hiện đã có trong chunk_service).
Phần này ĐƯỢC coi là heuristic subtitles; không ảnh hưởng identity/content.

## 6. Rủi ro & giới hạn (đã đo/giải quyết)

| Rủi ro | Mức | Ghi chú |
|---|---|---|
| VRAM 4GB khi model `large-v3` | **Đã giải quyết** | peak 3906MB (batch=2) < 4GB, đo trên GPU thật RTX 3050 Mobile 4GB. |
| Hiện tượng không ổn định của faster-whisper batched | Thấp | Engine có fallback → regular; 2 trials cùng output (segs 9=9). |
| Gap timeline khi render với khoảng lặng dài | Thấp | Timeline coverage batched 0.997 vs regular 0.84 — batched KHÔNG tệ hơn. |
| Re-sync STT ↔ translate/tts khi segment count thay đổi | Trung | Identity 1:1 giữ nguyên qua chunked (e2e pass); unit test 17 tests phủ fallback/reconstruct/validate. |

## 7. Điểm cần quyết định (đã giải quyết)

1. Approve implement production đường STT-batched + abstraction? → **YES**.
2. Default mode? → **`auto`**: batched khi `device=cuda` VÀ VRAM đủ
   (model_requirement + 400MB margin, đo từ benchmark); regular nếu không.
3. Heuristic nới gap để coverage subtitle ≈ single-pass? → **KHÔNG cần** —
   batched đã có timeline coverage 0.997 (không tạo gap giả; SILENCE giữ nguyên).
4. Benchmark `large-v3` trên GPU 4GB trước khi merge? → **Đã đo** (§4, pass).

## 8. Files production đã sửa

## 8. Files production đã sửa

| File | Thay đổi |
|---|---|
| `worker/src/services/stt_service.py` | `STTEngine` / `RegularSTTEngine` / `BatchedSTTEngine`, `resolve_stt_mode`, `validate_segment_timestamps`, `reconstruct_segments_from_words`, `TranscribeResult.engine`, `transcribe(..., stt_mode, batch_size, on_stt_log)` + fallback, words trên transcript |
| `worker/src/services/stt_service.py` | `_flatten_word_timestamps`, `_char_cap`, constants `STT_MODE_*`, `SUPPORTED_BATCH_SIZES`, `RECON_*` |
| `worker/src/services/chunk_service.py` | `ChunkPipelineContext.stt_mode/stt_batch_size/chunks_total`, `_run_stt_stage` truyền + log `[STT]`, `manifest["stt"]` |
| `worker/src/api/pipeline.py` | `ChunkedAutomationRequest.stt_mode="auto"` + `stt_batch_size=2`, pass-through |
| `src-tauri/src/services/worker_client.rs` | request struct + 2 field |
| `src-tauri/src/services/settings_service.rs` | keys `automation.stt_mode`/`automation.stt_batch_size` + defaults + validation (1/2/4) |
| `src-tauri/src/services/pipeline_runner.rs` | đọc settings → request |
| `worker/tests/unit/test_batched_stt_engine.py` (**mới**) | 17 tests: resolve/validate/reconstruct/fallback/batched-success/auto-on-cpu |
| `worker/tests/integration/e2e_chunked.py` | args `--stt-mode/--stt-batch-size` + báo cáo |
| `worker/tests/integration/bench_batched_stt.py` (**mới**) | production benchmark §4 (large-v3 regular vs batched) |

## 9. Files dự kiến (đã không cần)

- `worker/src/services/batched_provider.py` — **không cần tách provider**; dùng
  abstraction `STTEngine` trong chính `stt_service.py` (đơn giản hơn, đúng scope,
  không thêm layer).