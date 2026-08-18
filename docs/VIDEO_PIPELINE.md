# VIDEO_PIPELINE.md — Video processing pipeline

## Classic (single-pass) pipeline

Đây là pipeline mặc định của Automation (chunked = off):

```
Extract audio → STT → Translation → TTS → Subtitle → Render → Validate → Cleanup
```

- Toàn bộ audio được xử lý tuần tự theo từng stage.
- Phù hợp video ngắn (< 5 phút).

## Chunked pipeline (30s parallel)

Khi bật **"Chunked processing (30s parallel)"** trong More Options, pipeline
chia video thành các chunk 30s (configurable 20/30/45/60s) và xử lý song song:

```
Extract audio (1 lần)
   ↓
30s chunks (overlap 2s context)          ← ChunkManager
   ↓
bounded worker pool (max_concurrency=4)  ← ChunkScheduler
chunk_i: STT → Translation → TTS → Subtitle
   ↓
per-chunk validation + retry (max 2)     ← retry riêng chunk lỗi
   ↓
order validation (missing/duplicate/order/gap)
   ↓
ordered assembly: merge segments/cues, concat TTS tracks
   ↓
timeline validation (tolerance 0.5s)
   ↓
final single encode → output/rendered.mp4
   ↓
FINAL VALIDATION (ffprobe: streams, duration, size)
   ↓
OUTPUT VERIFIED → CLEANUP (chỉ khi PASS cả hai)
```

### Cấu hình (Settings registry)

| Key | Default | Ý nghĩa |
|---|---|---|
| `automation.chunk_duration` | 30 | Giây mỗi chunk (20/30/45/60) |
| `automation.chunk_overlap` | 2 | Giây context overlap giữa chunk |
| `automation.chunk_concurrency` | 4 | Số chunk xử lý song song (1..8) |
| `automation.chunk_retries` | 2 | Số lần retry tối đa cho một chunk |

### Failure recovery

- Chunk lỗi → retry riêng chunk đó (không chạy lại toàn bộ).
- Retry hết → `FAILED_PERMANENTLY` → run dừng, không tạo output với chunk lỗi.
- Final validation FAIL → giữ toàn bộ temp artifacts để debug.
- Cleanup chỉ chạy khi validation PASS **AND** output verified.

### Shared STT model

Chunked pipeline dùng **một** faster-whisper model dùng chung cho mọi chunk
(cache theo model/device/compute-type trong `stt_service`). Tránh load ~3 GB
model mỗi chunk (OOM/network flake trên video dài). CTranslate2 hỗ trợ
concurrent inference trên cùng instance.

### Benchmark

Xem `docs/CHUNKED_PIPELINE_REPORT.md` — ladder 30–60s / 5 min / 40 min với
số liệu thực tế.
