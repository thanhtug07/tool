# AI_PIPELINE.md — AI stages (STT / Translation / TTS)

## Providers

Provider được quản lý bởi **Provider Manager** (`providers` table + registry) —
không hard-code provider nào trong UI/pipeline:

| Capability | Provider | Ghi chú |
|---|---|---|
| STT | `free` (faster-whisper) | Model tải qua HuggingFace, cache 5 GB local |
| Translation | `gemini` (cloud) / `local` / `free` / `mock` | Default `gemini` |
| TTS | `free` (edge-tts) / `local` (Piper) | Voice Library chung toàn app |

Chunked pipeline gọi **chính các service hiện tại** (không implement STT /
translation / TTS mới):

- `stt_service.transcribe` — faster-whisper, shared model (xem VIDEO_PIPELINE).
- `translation_service` + provider abstraction (`build_translation_provider`).
- `tts_service.synthesize_cues` — edge-tts / Piper qua Voice Library.

## Chunk context

Mỗi chunk được xử lý với **processing range** (có overlap 2s context) và
**logical range** (chỉ phần đưa vào timeline chính):

```
chunk.start .. chunk.end            → context cho STT/translation/TTS
chunk.logical_start .. logical_end  → phần giữ lại trên timeline cuối
```

Overlap KHÔNG bao giờ xuất hiện 2 lần trên timeline (clamp + merge dedupe).

## Silent chunk

Chunk không có speech là kết quả HỢP LỆ — không đóng góp segment/subtitle/
voice, không fail pipeline (unit-tested).

## Events / progress

Mọi stage đẩy event thật qua worker → Rust `ctx.log` → live log:

```
CHUNK_CREATED / CHUNK_STARTED i/N
STT_STARTED/COMPLETED · TRANSLATION_STARTED/COMPLETED · TTS_STARTED/COMPLETED
CHUNK_VALIDATING / CHUNK_VALID / CHUNK_FAILED / CHUNK_RETRYING
ASSEMBLY_STARTED/COMPLETED · FINAL_VALIDATION_* · OUTPUT_VERIFIED · CLEANUP_*
```

Không có progress giả lập.
