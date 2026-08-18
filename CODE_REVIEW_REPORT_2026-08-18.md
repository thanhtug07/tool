# Code Review Report — ToolTranslate (AI Video Localization Studio)

- **Ngày review:** 2026-08-18
- **Branch / commit:** `main` @ `6bc5d58` (sau đó còn WIP chưa commit ~80 file)
- **Phạm vi:** toàn bộ codebase — Rust backend (`src-tauri/`), Python worker (`worker/`), React frontend (`src/`), schemas, config, CI, docs.
- **Kích thước code đã rà:** Rust `src-tauri/src` ≈ 14.615 dòng · Frontend `src/` ≈ 13.958 dòng · Worker `worker/src` ≈ 9.206 dòng + tests ≈ 1.491 dòng.
- **Phương pháp:** đọc trực tiếp source theo module, đối chiếu quy ước trong `AGENTS.md` / `DEVELOPMENT.md` / `TASKS.md`, và chạy lại toàn bộ quality gates trên máy dev.

> Lưu ý: một agent phụ trong quá trình review đã từng sửa 5 vị trí trong worker; toàn bộ các thay đổi đó **đã được revert** để repo về đúng trạng thái trước review. Các vấn đề liệt kê dưới đây được **xác minh lại trực tiếp trên code hiện tại** (file:line chính xác), không dựa trên bất kỳ bản sửa nào của agent.

---

## 1. Tổng quan kiến trúc

Ứng dụng desktop 3 lớp, local-development-only (không đóng gói EXE/installer):

| Lớp           | Công nghệ                                               | Vai trò                                                                           |
| ------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Rust core     | Tauri 2 + rusqlite (bundled) + keyring                  | DB, job queue, pipeline runner, spawn/supervise worker, media scope, secrets, IPC |
| Python worker | FastAPI/uvicorn, faster-whisper, edge-tts/piper, ffmpeg | STT, dịch (gemini / local_llm / mock), subtitle, TTS, render, audio/logo/chunked  |
| Frontend      | React 19 + TS + Vite + Tailwind, vitest                 | Workspace studio, automation, custom tools, live log, settings                    |

Chuỗi pipeline: `transcribe → translate → subtitle → tts → render`, có thêm stage `audio`, `logo`, `chunk` (chunked 30s). Worker chạy looppback-only (`127.0.0.1`), auth bằng token 256-bit truyền qua stdin; Rust gọi HTTP/1.1 thủ công (không kéo reqwest/TLS) với timeout + giới hạn kích thước response.

**Điểm mạnh chung:**

- Phân tách 3 lớp rõ ràng, worker sidecar độc lập, mọi state pipeline là state thật từ backend (không fabricate).
- Bảo mật nhất quán: secret nằm ở OS credential vault (không trong DB/`.env`), URL scope `media://` chặn path ngoài `source_video_path`, command ffmpeg luôn là argument array (không shell string), token/secret không bao giờ bị log.
- Test ở cả 3 lớp với tỷ lệ khá tốt; có CI (frontend/rust/worker/licenses/security).

---

## 2. Kết quả quality gates (chạy lại trên máy dev)

| Gate                                       | Kết quả     | Ghi chú                                                                                                                                                                                  |
| ------------------------------------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `npm run typecheck` (tsc --noEmit)         | ✅ PASS     | —                                                                                                                                                                                        |
| `npm run lint` (eslint)                    | ✅ PASS     | —                                                                                                                                                                                        |
| `npm run format:check` (prettier)          | ❌ **FAIL** | 20 file chưa format: `src/App.tsx`, `src/pages/**`, `src/workspace/**`, `src/lib/voiceLibrary.ts`, `LiveLog.tsx`, `PERFORMANCE_AUTOMATION_PROGRESS.md`, `TASK_AUTOMATION_PINELINE.md`, … |
| `npm run test` (vitest)                    | ✅ PASS     | 27 files / **200 tests**                                                                                                                                                                 |
| `cargo check`                              | ✅ PASS     | Finished dev profile                                                                                                                                                                     |
| `cargo test`                               | ✅ PASS     | **199 passed, 1 ignored**, 0 failed                                                                                                                                                      |
| Worker `pytest tests/unit`                 | ✅ PASS     | **83 passed** (7.69s)                                                                                                                                                                    |
| Worker smoke `python -c "import src.main"` | ✅ PASS     | smoke OK                                                                                                                                                                                 |

→ **Gate duy nhất đỏ là `format:check`** (20 file, chủ yếu là các file thuộc WIP workspace mới: custom tools, voice picker, SubtitleEditorView, v.v.). Đây là chính các file đang nằm trong khối chưa commit.

---

## 3. Rust backend (`src-tauri/`)

### 3.1 Điểm tốt (đã đọc trực tiếp)

| File                                       | Nhận xét                                                                                                                                                                                                                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/services/worker_client.rs`            | Client HTTP tự viết, không dependency nặng. Timeout phân tầng (probe 2–3s, pipeline 4h, progress 1s — `worker_client.rs:21-43`), cap header 16KB / body 16MB (`:24-27`), bearer token chỉ trong header `Authorization`, error envelope lấy đúng schema `api.schema.json`. |
| `src/services/worker_manager.rs`           | Spawn qua stdin token, port ephemeral loopback, state machine, restart sau crash, `READY <token>` handshake, redaction token khi log.                                                                                                                                     |
| `src/services/pipeline_runner.rs`          | Chạy lần lượt các stage, cancel lan tới `/v1/jobs/cancel`, live progress map vào `job_progress` window, retry theo backoff.                                                                                                                                               |
| `src/services/job_service.rs`              | Hàng đợi FIFO, retry 1s/5s/30s, resume job đang dang dở sau crash (đọc lại từ DB).                                                                                                                                                                                        |
| `src/services/cache_service.rs`            | Cache content-addressed SHA-256, key `tr:`/`audio:` khớp parity với `worker/src/services/cache.py` (`cache.py:14,54`).                                                                                                                                                    |
| `src/services/settings_service.rs`         | Whitelist key (`SETTINGS_KEYS`), min/max quota hợp lý (1GB–1TB), `get_all` luôn trả đủ key kèm default.                                                                                                                                                                   |
| `src/security/secret_store.rs`             | OS credential vault, allow-list provider gemini/local/openai.                                                                                                                                                                                                             |
| `src/services/hardware_probe.rs`           | Đa nguồn (nvidia-smi / WMI / ffmpeg -encoders), best-effort, timeout mọi subprocess — không crash khi thiếu tool.                                                                                                                                                         |
| `src/media.rs`                             | Scope `media://` giới hạn thư mục project, hỗ trợ range 64MB.                                                                                                                                                                                                             |
| `src/db/migrations.rs`                     | Migrations rõ ràng, kèm tests migration.                                                                                                                                                                                                                                  |
| `src-tauri/Cargo.toml` / `tauri.conf.json` | Pinned deps bản cụ thể (tauri 2.11.5, rusqlite 0.40.2 bundled), CSP chặt, devUrl `localhost:1420`.                                                                                                                                                                        |

### 3.2 Nhận xét / điểm cần lưu ý

- Không phát hiện lỗi nghiêm trọng (SEV-cao) trong Rust.
- `settings_service.rs:51-56` hard-code model mặc định (`ai.model`="large-v3", `api.gemini.model`="gemini-flash-lite-latest") — **trái tinh thần AGENTS.md**: "Không hard-code: model name". Đây là defaults trong registry có thể ghi đè qua Settings nên ảnh hưởng thấp, nhưng nên cân nhắc đưa vào cấu hình mặc định có chú thích rõ.
- WIP chưa commit: `src-tauri/src/commands/media.rs` và `commands/models.rs` là file mới (untracked) — khối đang phát triển dở, cần commit sớm cùng gói format.
- Quy mô `worker_client.rs` = 1.609 dòng cho một HTTP client thủ công — dễ duy trì hơn bằng một crate nhỏ chuyên dụng nếu có thêm method về sau (hiện đang ổn về bảo mật/tính ổn định).

---

## 4. Python worker (`worker/src/`)

### 4.1 Điểm tốt

- `main.py` chạy sidecar đúng quy ước: stdin mang token, stdout `READY <token>`, watcher `SHUTDOWN`/EOF, warm-up AI stack nền không chặn health (`main.py:78-112`).
- Render có validation hậu kỳ nghiêm túc (probe output, so resolution/FPS/duration, kiểm tra burn-in) — `render_service.py` docstring `:1-38`.
- `cache.py` cache key content-addressed + versioned (`tr_key` có `glossary_ver` + `rules_ver`).
- Security model render: copy subtitle/watermark vào workdir tên an toàn, escape filter-graph, error message không lộ path.
- Error taxonomy dùng mã `E_*` thống nhất xuyên từ worker → Rust (`ErrorEnvelope`) → frontend.
- Chunked pipeline có validate timeline + manifest (`chunk_service.py`).

### 4.2 Vấn đề đã xác minh (file:line trên code hiện tại)

**SEV-trung bình:**

1. **`render_service.py:317` — audio `-map` sai index khi kết hợp image watermark.**
   `args += ["-map", f"{args.count('-i') - 1}:a"]` đếm số `-i` trong toàn args; nếu `filter_graph.extra_input` (ảnh watermark) được thêm sau audio track (`:303-305`), chỉ số trở thành index của ảnh thay vì audio → `-map 2:a` không khớp stream audio (ffmpeg báo lỗi hoặc mất track audio). Chỉ kích hoạt khi **đồng thời** có voice/audio track + image watermark. Sửa: lưu index của input audio rõ ràng thay vì đếm `-i`.

2. **`render_service.py:1047` — thiếu output directory → lỗi thô không phải `E_RENDER_INVALID`.**
   `workdir = Path(tempfile.mkdtemp(..., dir=str(Path(output_path).parent)))` nằm **ngoài** khối `try` (`:1049`) và không có `makedirs` trước đó. Nếu thư mục đích không tồn tại, `FileNotFoundError` nguyên bản trồi ra ngoài thay vì map về `E_RENDER_INVALID` (taxonomy đang cam kết tại `:26-29`). Hậu quả: API trả lỗi không đúng mã, frontend không phân loại được.

3. **`tts_service.py:205-212` — default piper cho `en`/`ja`/`ko` là **giọng Việt** `vi_VN-vais1000-medium`.**
   `_DEFAULT_VOICE["en"]["piper"] = "vi_VN-vais1000-medium"` (tương tự `ja`, `ko`), trong khi engine `piper` chỉ có voice `vi`/`zh`. Người dùng chọn TTS local với target tiếng Anh/Nhật/Hàn sẽ nhận đúng giọng nói tiếng Việt. Đồng thời `_DEFAULT_VOICE_FALLBACK` hard-code `vi-VN-HoaiMyNeural` cho mọi ngôn ngữ chưa biết. Đây vừa là lỗi chất lượng vừa vi phạm "không hard-code voice/model" (AGENTS.md).

4. **`translation_service.py:63-64` — TranslationMemory key thiếu `rules_ver`.**
   `cache.py:54` đã định nghĩa `tr_key(..., glossary_ver, rules_ver)` nhưng `TranslationMemory._key` chỉ dùng `(hash, target, glossary_ver, model)` — **rules** (ảnh hưởng trực tiếp đến bản dịch vì được đưa vào context của provider) không tham gia vào key. Khi người dùng đổi translate-rules, memory vẫn trả kết quả cũ → subtitle dính bản dịch stale. Chú ý: file-level `tr:` cache trong `cache.py` có `rules_ver` nhưng service thực tế lại dùng TM in-memory, tạo ra 2 cơ chế cache không đồng bộ.

5. **`chunk_service.py:514-548 / 1140-1178` — nguy cơ lệch `segment_id` `seg_N` giữa transcript và translation.**
   `merge_segments` dedup theo `(start, end, source_text)` còn `merge_cues` dedup theo `(start, end, translated_text)` rồi cả hai đều renumber lại từ `seg_0` (`:530-533`, `:1144`, `:1171`). Nếu một segment bị drop ở một trong hai phía (dedup khác nhau giữa nguồn và bản dịch), mọi `seg_i` sau điểm đó sẽ **dịch phải một offset** → subtitle stage match theo `segment_id` sẽ ghép sai. Thêm nữa: bản dịch trong từng chunk dùng `seg_{idx}` cục bộ (`:916`) trong khi artifact cuối renêu toàn cục — chỉ đúng khi hai danh sách có cùng thứ tự/độ dài. Nên: dùng key nhất quán (vd. start-time) hoặc validate id trước khi ghép.

**SEV-thấp:**

6. `stt_service.py:285` sinh `"id": f"seg_{idx}"` (_chunk-local_) ngay tại nguồn — dễ lan truyền lệch id nếu dùng làm id toàn cục.
7. Worker chạy Python 3.13.7 trên dev trong khi CI pin canonical 3.11 (`ci.yml:83-85`) — nếu dính API mới của 3.12+ thì tới khi đóng gói mới lộ; nên pin bản local trùng CI.

---

## 5. Frontend (`src/`)

### 5.1 Điểm tốt

- `store/jobs.tsx`: single source of truth, poll 3s + event-driven merge; `refresh` chống trùng in-flight bằng `refreshInFlight` ref (`:45-61`).
- `api/invoke.ts` / `bridge.ts`: `safeInvoke` chuyển lỗi IPC ngoài Tauri thành lỗi có thể bắt — chạy được ngoài webview/tests.
- `VideoPreview.tsx`: overlay caption là hàm thuần của playhead + cues; rAF loop bám playhead; `videoContentRect` tính đúng rect nội dung `object-fit: contain` để kéo caption chuẩn vị trí burn.
- `StudioWorkspace.tsx`: session persist/hydrate theo project với `hydratedProjectId` chống ghi đè chéo giữa 2 project (`:207-286`); undo/redo cue qua backend thật (`diffCues`/`pushUndo`).
- `LiveLog.tsx`: log từ `job:log` thật + backfill từ DB, auto-scroll có phân biệt "đang ở đáy", ETA từ vận tốc thật.

### 5.2 Vấn đề đã xác minh (file:line)

**SEV-trung bình:**

1. **`src/components/VideoPreview.tsx:167-175` — edit cue → player bị reset.**
   Effect reset `currentTime=0` + `playing=false` + `loading` có dependency `cues`. Mỗi lần sửa cue (updateCue → refreshCues tạo array mới) video nhảy về 0 và dừng phát. Đúng ý định là reset theo `videoUrl` (source mới), không phải theo `cues`.

2. **`src/workspace/StudioWorkspace.tsx:358-378` — seed TTS từ Settings có thể ghi đè session của project.**
   Effect chạy 1 lần khi mount, gọi async `getSettings()`, rồi `setTtsEngine(...)` + `setVoice(...)` **không có guard `sessionRestoredRef`** (guard chỉ đặt cho `setDubAudio`, `:370`). Khi app mở thẳng vào một project đã lưu session (engine/voice khác Settings), kết quả Settings resolve sau cùng và ghi đè engine/voice vừa restore. — Cùng vùng này còn một effect thứ 2 nữa phụ thuộc `[ttsEngine]` (`:381-404`) cùng ghi `voice`, tạo 2 luồng async ghi `voice` không đồng bộ.

**SEV-thấp:**

3. **`src/pages/Automation/LiveLog.tsx:524` — cap chiều cao console cứng 200px nuốt tính năng kéo resize.**
   `style={{ height: Math.min(height, 200) }}` trong khi drag cho phép 160–720px (`:39-40`). Kéo resize trên 200px không còn tác dụng gì → trông như chức năng hỏng.

4. **`src/pages/Automation/logHelpers.ts:81` + `LiveLog.tsx:92,107` — id log trùng nhau (backfill vs live).**
   `backfillFromJobs` tạo id bắt đầu từ `0` (`logHelpers.ts:81`) trong khi live events dùng `nextId` cũng bắt đầu `0` → `key={e.id}` (`LiveLog.tsx:530`) trùng key, React có thể dùng sai DOM node khi list trộn 2 nguồn.

5. **`src/workspace/ConfirmDialog.tsx:8` — docstring nói "Escape or the backdrop to dismiss" nhưng không có handler Escape** (chỉ có backdrop `onPointerDown` tại `:35-37`). Doc lệch code.

6. **EHàng loạt UUID sentinel `EMPTY_PROJECT = "00000000-0000-4000-8000-000000000000"` lặp 3 nơi** — `src/pages/Dictionary/index.tsx:13`, `src/pages/Project/PreviewView.tsx:8`, `src/pages/Project/SubtitleEditorView.tsx:6`. Nên đưa vào một chỗ dùng chung.

7. **Hai type cùng tên `AutomationOptions` khác nghĩa** — `src/workspace/types.ts:79` (controller context, có setters/providerOptions) và `src/pages/Automation/automation.ts:103` (snapshot run : videoPath/stepConfig/enabledStages). Hiện không xung đột biên dịch (khác module) nhưng là "bom nổ chậm" khi ai đó import lẫn lộn.

8. **`src/lib/pipeline.ts:17-25` — `STAGE_LABELS` thiếu stage `tts`, `audio`, `logo`, `chunk`** (chỉ có 5 key). `LiveLog.tsx:616-624` đã có riêng bảng đầy đủ — nên thống nhất về một nguồn. Hệ quả hiện tại nhỏ (fallback hiện raw key).

---

## 6. Schemas, config, CI, docs

- **Schemas** (`schemas/*.schema.json`): 8 schema + thư mục `examples/valid|invalid` — đúng vai single-source-of-truth (Rust `worker_client.rs` và worker `schemas.py` tham chiếu). WIP đang sửa `translation.json` examples (chưa commit).
- **CI** (`ci.yml`): đủ 5 job — frontend, rust (fmt/check/clippy/test), worker smoke (3.11), licenses (cargo-deny), security (gitleaks). Pinned node 24 / rust 1.96 / python 3.11. **Gap nhỏ:** CI không chạy `npm run format:check` ở... thực tế có (`:36-38`). Hạn chế: khối worker test (83 test) **không chạy trong CI** — CI chỉ smoke import (`:90-91`); các tiến trình render/audio/chunk có thể regression mà CI không bắt.
- **Docs**: `DEVELOPMENT.md`, `TASKS.md`, `docs/AI_PIPELINE.md`, `CHUNKED_PIPELINE_AUDIT.md`, `FINAL_FUNCTIONAL_AUDIT.md`… — nhiều tài liệu, thể hiện rõ quy trình phase đã thực hiện (đối chiếu `git log` thấy chuỗi commit theo Gate/Phase).

---

## 7. Tổng hợp ưu tiên

### Cần xử lý trước (SEV-trung bình, nên làm trước khi "đóng gói" phase nhập liệu)

| #   | Vấn đề                                                | Vị trí                                          | Tác động                                                |
| --- | ----------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| 1   | `-map` audio sai index khi có image watermark         | `worker/src/services/render_service.py:317`     | Video dub + watermark mất/không có audio                |
| 2   | Thiếu output dir → lỗi thô thay vì `E_RENDER_INVALID` | `render_service.py:1047`                        | API contract vỡ, UI hiểu sai lỗi                        |
| 3   | Piper default `en/ja/ko` → giọng Việt                 | `tts_service.py:205-212`                        | TTS local sai ngôn ngữ                                  |
| 4   | TM key thiếu `rules_ver` (2 cơ chế cache lệch nhau)   | `translation_service.py:63-64` vs `cache.py:54` | Kết quả dịch stale khi đổi rules                        |
| 5   | Chunked: nguy cơ lệch `seg_N` khi dedup khác nhau     | `chunk_service.py:514-548,1140-1178`            | Subtitle/translation lệch về sau khi có segment bị drop |
| 6   | Edit cue reset player (deps `cues`)                   | `src/components/VideoPreview.tsx:167-175`       | UX vỡ khi sửa subtitle                                  |
| 7   | Seed TTS ghi đè session project                       | `src/workspace/StudioWorkspace.tsx:358-378`     | Voice/engine reset sai khi mở project                   |
| 8   | `npm run format:check` đỏ (20 file)                   | nhiều file WIP                                  | Gate bắt buộc theo AGENTS.md chưa pass                  |

### Nên làm (SEV-thấp / vệ sinh)

- Height cap 200px phá resize (`LiveLog.tsx:524`).
- Id log trùng (backfill vs live) → key React trùng (`logHelpers.ts:81`).
- `ConfirmDialog` thiếu Escape so với doc (`ConfirmDialog.tsx:8`).
- `EMPTY_PROJECT` UUID lặp 3 nơi.
- Nhân đôi type `AutomationOptions`.
- `STAGE_LABELS` thiếu `tts/audio/logo/chunk` (`pipeline.ts`).
- Hard-code model names trong `settings_service.rs:51-56`.
- Cân nhắc đưa pytest worker vào CI (hiện chỉ smoke).

### Điểm không vướng mắc

- Không phát hiện lỗi bảo mật (secret leak / credential trong repo / log); `git status` được rà lại, không có `.env`-like file trong working tree (gitleaks chưa chạy local do tool không có trên máy — CI job `security` đã phủ).
- Toàn bộ thay đổi ngoài phạm vi review của agent trước đó đã được revert sạch (verify: `git status -- worker` khớp trạng thái trước review; worker smoke + 83 test pass).
