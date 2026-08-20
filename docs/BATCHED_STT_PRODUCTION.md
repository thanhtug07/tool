# BATCHED STT — Production Implementation Notes (BATCHED_STT)

> Trạng thái: **ON PRODUCTION PATH** — batched transcription được bật qua engine
> abstraction với `stt_mode=auto` (batched khi CUDA + VRAM đủ, regular otherwise),
> fallback an toàn về regular, timestamp validation trước khi output xuống
> pipeline. Bản phác thảo + benchmark `small` ở `docs/BATCHED_STT_PROPOSAL.md`.
> Mọi con số bên dưới đo trên RTX 3050 Mobile 4GB / Windows / CUDA (ctranslate2
> → 1 device), faster-whisper 1.2.1, worker Python (chốt 3.11).

## 1. Vì sao có cái này

`faster-whisper` có `BatchedInferencePipeline` (kiểu whisperX): gom các chunk audio
thành batch → encoder chạy song song → nhanh hơn nhiều so với single-pass
`transcribe` trên GPU. Nhưng nó trả segment là các *window 30s* thô (có word
timestamps), không giống segment canonical của single-pass. Task BATCHED_STT đưa
mode này lên đường production một cách an toàn.

## 2. Kiến trúc

```
transcribe(..., stt_mode, batch_size)
   └─ resolve_stt_mode(auto|regular|batched, device, model, vram) -> (mode, reason)
        auto: batched ⟺ device==cuda AND vram_mb is not None AND
              vram_mb >= MODEL_VRAM_REQUIREMENTS_MB[model] + BATCHED_VRAM_MARGIN_MB (400MB)
   └─ dispatch:
        regular → RegularSTTEngine.transcribe(...)
        batched → BatchedSTTEngine.transcribe(...)   // + validate + fallback
```

- `STTEngine` là abstract contract; cả 2 engine trả `TranscribeResult` (cùng
  `transcript` schema, thêm `engine` + segment `words` khi có).
- `BatchedSTTEngine`: `BatchedInferencePipeline(model)` với
  `beam_size=5, vad_filter=True, word_timestamps=True, batch_size∈{1,2,4}`;
  gom word timestamps → `reconstruct_segments_from_words` (offline, deterministic:
  cắt theo dấu câu / silent gap / max 8s span / char cap) → validate → output.
- `_run_stt_stage` (chunk_service) gọi qua `transcribe(..., stt_mode=ctx.stt_mode,
  batch_size=ctx.stt_batch_size)`; mọi chunk stream `[STT]` log:
  `Mode/Model/Batch size/Chunk: i/total/RTF` (mode = engine THỰC dùng sau auto).

## 3. Fallback (bắt buộc, không fail job)

Bắt mọi lỗi từ batched (OOM/mất CUDA lib/mô hình không support/timestamps hỏng),
log đúng 3 dòng rồi chạy regular:

```
[BATCHED_STT] Failed: <exception>
[BATCHED_STT] Reason: <one line>
[STT] Falling back to regular mode
```

- Fallback **bao gồm cả rejection bởi `validate_segment_timestamps`** — output hỏng
  không bao giờ xuống translate/subtitle/assembly.
- Các lỗi của engine regular giữ nguyên hành vi cũ (NO_SPEECH → chunk rỗng hợp lệ;
  còn lại → ChunkFailedError).

## 4. Bảo vệ chất lượng timeline (không tạo dữ liệu giả)

- **Không nới/gap-fill**: SILENCE giữ nguyên là gap (không "invent" transcription);
  không tạo segment giả, không đổi text.
- `validate_segment_timestamps`: bác bỏ `start<0`, `end<start`, không monotonic,
  duplicate, overlap (>0.001s). Tolerance 0.001s để kế thừa round(3) của
  reconstruction (no false-positive trên boundary hợp lệ).
- `reconstruct` ưu tiên giữ nguyên thứ tự + nội dung; span cap 8s/segment và char
  cap theo ngôn ngữ để giới hạn subtitle dài.

## 5. Benchmark `large-v3` (DoD gate — đã đo)

`worker/tests/integration/bench_batched_stt.py` — 60s fixture, 2 trials/cấu hình,
chạy đúng engine production:

| Config | RTF | Wall | VRAM | Segs | Word cov | Timeline cov |
|---|---|---|---|---|---|---|
| regular | 0.4072 | 24.43s | 3924MB | 13 | 0.00 | 0.840 |
| batched batch=1 | 0.3287 | 19.72s | 3746MB | 9 | 1.00 | 0.997 |
| **batched batch=2** | **0.2533** | 15.20s | 3906MB | 9 | 1.00 | 0.997 |
| batched batch=4 | 0.2402 | 14.41s | 3906MB | 9 | 1.00 | 0.997 |

Kết luận có cơ sở:
1. **RTF giảm ~38–41%** (batch=2/4) so với regular trên chính mô hình `large-v3`.
   batch=2 là điểm cân bằng tốt (batch=4 chỉ thêm ~4% nhưng tăng VRAM).
2. **Chất lượng không bị đánh đổi**: word coverage 1.0 vs 0.0 (regular không nhận
   word timestamps), timeline coverage 0.997 vs 0.84. Segment ổn định (9=9 qua 2
   trials) — không có phát hiện regress nội dung.
3. **VRAM an toàn GPU 4GB**: peak 3906MB (batch=2) < 4096MB; chênh 200MB với
   regular không phải blocker. Auto mode thêm margin 400MB nội bộ, nên nếu máy
   GPU 4GB đang dùng gần đầy → tự rơi về regular.
4. **E2E pass**: `e2e_chunked.py --stt-mode batched --stt-batch-size 2` trên CUDA →
   identity 1:1, render + FFprobe + QC (video not black, có âm thanh) pass.

Evidence: `%TEMP%\tc_bench_batched_*\bench_batched_stt.json`.

## 6. Giá trị config (production path)

| Key | Default | Hợp lệ | Nghĩa |
|---|---|---|---|
| `automation.stt_mode` | `auto` | `auto`/`regular`/`batched` | engine cho stage STT chunked |
| `automation.stt_batch_size` | `2` | `1`/`2`/`4` | batch size của batched engine |

Thay đổi được load từ settings service (Rust) → `ChunkedAutomationRequest` →
worker. Không thêm UI toggle (config nội bộ đủ; out-of-scope MVP).

## 7. Đã verify / còn lại

**Verify:**
- `worker/tests/unit/test_batched_stt_engine.py` (17 tests) + full unit suite 146 pass.
- `cargo check` + `cargo test` (199 pass).
- Worker import smoke + `compileall`.
- E2E batched trên CUDA pass (identity + render + QC).
- Benchmark `large-v3` regular vs batched (bảng §5).

**Còn lại (ghi chú không blocker):**
- Integration `batched → translate → subtitle → assembly` đã cover qua e2e_chunked;
  nếu muốn cứng hơn có thể thêm assert `manifest["stt"].mode == "batched"` trong
  e2e (hiện rely trên worker log).
- Benchmark đã chạy với `large-v3`; máy khác (VRAM khác) vẫn được auto-mode bảo vệ
  bằng margin VRAM.
- `manifest["stt"]` được viết vào response manifest trước khi dump cache (thứ tự
  viết: manifest dump xảy ra trước khi set `perf/stt/artifacts` — thuộc về file
  chunk_manifest persist; không ảnh hưởng API response mà Rust dùng).

## 8. Quyết định: default = auto

- Không hard-code "batched luôn luôn" — auto mode đã đủ an toàn (CUDA + VRAM) và
  benchmark `large-v3` xác nhận lợi ích RTF trên GPU của máy dev.
- Lý do KHÔNG chọn regular mặc định: bị mất 38% RTF và 0 word timestamps; không có
  lợi ích bảo mật/chất lượng gì để đổi lấy trên GPU có VRAM đủ.
- Lý do KHÔNG chọn batched luôn luôn: VAD + window có thể không hợp trên máy rất
  yếu; regular là fallback lâu đời. `auto` giữ cân bằng.