# SYSTEM ARCHITECTURE — AI Video Localization Studio

> Tài liệu mô tả **kiến trúc hiện tại của project dựa trên code thực tế**.
> Không mô tả feature chưa implement. Mỗi claim được định vị bằng `file:line`.
>
> Chú thích trạng thái dùng trong tài liệu:
> - `NOT VERIFIED` — chưa xác minh được trong code hiện tại.
> - `PARTIAL` — chức năng/component đang được implement một phần.
> - `IMPLEMENTED BUT NOT ACTIVE` — có code nhưng chưa được wire vào luồng sử dụng.
> - `NOT IMPLEMENTED` — hoàn toàn chưa có.
>
> Ngày biên soạn: 2026-08-18. Nguồn: repository hiện tại (working tree).

---

# 1. SYSTEM OVERVIEW

## App là gì

**AI Video Localization Studio** là ứng dụng desktop để **localization video**: nhận một video có lời thoại, trích lời (STT), dịch, tạo phụ đề, lồng tiếng (TTS), render lại video có phụ đề/voice track, và export kết quả.

- Desktop app Tauri **2** (Windows).
- Kiến trúc 3 lớp: **Frontend React/TS** (UI) → **Rust core** (Tauri IPC + orchestration + SQLite) → **Python worker sidecar** (FastAPI loopback, chạy toàn bộ AI + FFmpeg).
- **Local-development-only**: không đóng gói EXE/installer/updater (`bundle.active: false`, `src-tauri/tauri.conf.json:59`; `DEVELOPMENT.md`). Worker chạy từ Python source tree (`python -m src.main`), không có bundled `worker.exe` (`src-tauri/src/lib.rs:113-118`).

## Người dùng làm gì

1. Choose/import một video (`src/workspace/StudioWorkspace.tsx:543-553`).
2. Chọn ngôn ngữ, provider dịch, voice, tick chọn các tuỳ chọn (burn subtitles, dub, logo removal, chunked, watermark…).
3. Chạy automation → pipeline chạy tuần tự từng stage hoặc chunked song song.
4. Preview original/result, chỉnh phụ đề bằng timeline/editor (có undo/redo), nghe voice preview.
5. Export video (QC bằng ffprobe) + subtitle (`srt/vtt/ass`).

## Luồng tổng thể

```text
User
 ↓
Frontend (React + Vite, src/)
 ↓  Tauri IPC invoke + events (job:status, job:log)
Application Layer (Rust core, src-tauri/src)
 ↓  Loopback HTTP 127.0.0.1, ephemeral port + bearer token
Python Worker (FastAPI, worker/src)
 ↓
AI Pipeline services (STT / translate / subtitle / TTS / render)
 ↓  subprocess
FFmpeg / faster-whisper / edge-tts / llama-server
 ↓
Output Video + Subtitles in project dir, export qua worker (`/v1/export/*`)
```

---

# 2. TECH STACK & DEPENDENCIES

## Frontend (`package.json`)

- React 19.2.8, react-dom 19.2.8, TypeScript 6.0.3, Vite 8.2.1 (`package.json:22-23,41,43`).
- Runtime deps: `@tauri-apps/api` 2.11.1, `@tauri-apps/plugin-dialog` ^2.7.2, `@radix-ui/react-slot`, `class-variance-authority`, `clsx`, `lucide-react`, `tailwind-merge` (`package.json:19-27`).
- Dev: `vitest` 4.1.10, `eslint` 10.8.1, `typescript-eslint`, `prettier`, `@tailwindcss/vite`, `tw-animate-css`, `@tauri-apps/cli` 2.11.4 (`package.json:29-45`).
- Test env: **`node`** (không jsdom/happy-dom) — mọi component test dùng `renderToStaticMarkup` (`vite.config.ts:26-30`).
- Scripts: `dev`/`build`/`typecheck`/`lint`/`format`/`format:check`/`test`/`tauri` (`package.json:7-17`). `tauri` = wrapper `scripts/tauri.cjs` (chỉ prepend cargo bin vào PATH).

## Rust core (`src-tauri/Cargo.toml`)

- `tauri` 2.11.5 (feature `protocol-asset`), `tauri-plugin-dialog` `"2"` (`Cargo.toml:18,21`).
- `rusqlite` 0.40.2 `bundled` (SQLite build từ source, self-contained) (`Cargo.toml:29`).
- `keyring` `"3"` features `["windows-native"]` — OS credential vault (`Cargo.toml:34`).
- `serde`/`serde_json`, `log`, `getrandom` 0.2 (`Cargo.toml:22-26`).
- Library crate-type `["lib","cdylib"]` (`Cargo.toml:11-12`); không có HTTP client nặng (WorkerClient tự viết HTTP/1.1 trên `TcpStream` — `src/services/worker_client.rs`).

## Worker (`worker/pyproject.toml`)

- `requires-python = ">=3.11,<3.14"` (`worker/pyproject.toml:5`).
- Deps: `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, `faster-whisper>=1.1,<2`, `jsonschema>=4.21,<5`, `google-genai>=1.0,<3` (`worker/pyproject.toml:6-16`).
- Optional `[cuda]`: `nvidia-cublas/cudnn/cuda-runtime/cuda-nvrtc` (worker tự fallback CPU nếu thiếu) (`worker/pyproject.toml:18-28`).
- Không khai `pytest`/test extra (`PARTIAL` — unit test chạy được nhưng không có config pytest trong pyproject).

## CI (`/.github/workflows/ci.yml`)

- 5 jobs: `frontend` (typecheck→lint→format:check→test→build, Node 24), `rust` (fmt→check→clippy `-D warnings`→test, Rust 1.96.0), `worker` (Python 3.11, `pip install -e "."`, **chỉ smoke import — không chạy pytest**), `licenses` (cargo-deny), `security` (gitleaks).

---

# 3. TÀI NGUYÊN DÙNG CHUNG (SCHEMAS)

`schemas/*.json` là **single source of truth** (draft 2020-12), kiểm chứng chéo 3 lớp: Rust contract tests, Python jsonschema/Pydantic, TS types. 8 schema:

| File | Nội dung (`schema` mô tả) |
|---|---|
| `api.schema.json` | Container contract HTTP + sidecar lifecycle (`$defs`: HealthStatus, HealthResponse, WorkerState, ErrorEnvelope code `^E_[A-Z0-9_]+$`) |
| `job.schema.json` | Job status (`id` `^job_[0-9]+$`, type/status enum, progress 0..1) |
| `media.schema.json` | MediaMetadata từ ffprobe (`schema_version:1`, Rotation 0/90/180/270, Rational) |
| `model.schema.json` | ModelRegistry entry (`id`, checksum 64-hex hoặc rỗng, `supported_backend` enum) |
| `project.schema.json` | Project (`id` UUID v4, status draft/analyzed/transcribed/translated/rendered) |
| `subtitle.schema.json` | Subtitle doc (`style`, `cues`, `output`) |
| `transcript.schema.json` | Transcript doc (`segments` `seg_[0-9]+`, confidence) |
| `translation.schema.json` | Translation doc (`blocks`, segment_id `seg_[0-9]+`) |

Examples: `schemas/examples/valid/` (10 file) + `schemas/examples/invalid/` (10 file). Wire table: `worker/tests/unit/test_schema_examples.py:27-38`. Rust contract tests đọc trực tiếp `schemas/examples/valid` (`src-tauri/src/services/contract_tests.rs:16`).

---

# 4. RUST CORE (Application Layer)

## 4.1 Entry & startup

- `src-tauri/src/main.rs` → `lib.rs::run()`.
- `.setup()` (`lib.rs:112-189`):
  - Khởi tạo `WorkerManager` (spawn Python worker) (`lib.rs:118-126`).
  - `SystemInfo` cached (`lib.rs:129`).
  - `ProjectService::open(app_data_dir)` (`lib.rs:135-136`).
  - Runtime mở rộng **asset protocol scope** cho mọi project đã đăng ký (`lib.rs:138-144`; `commands/media.rs:206-213`).
  - Đăng ký SecretStore / SettingsService / SubtitleService / DictionaryService / ProviderService / CacheService / JobService (`lib.rs:147-188`).
- **IPC commands đăng ký** (~50 lệnh) trong `invoke_handler` (`lib.rs:59-111`): `ping`, `system.hardware/reveal`, `worker.get_worker_state/restart`, `project.*` (8: create/open/list/save/delete/rename/updateSettings/findBySourceVideo), `job.*` (6: submit/get/list/list_all/cancel/retry), `dictionary.*` (7), `subtitle.*` (3), `export.*` (2), `media.probe`, `pipeline.artifact_paths`, `models.*` (3), `settings.*` (get_all/set + `settings.voices`, `settings.ttsPreview`), `secrets.*` (3), `provider.*` (8).
- **Asset protocol**: frontend dùng `convertFileSrc` → `asset://` (`src/api/media.ts:26`); scope project dirs runtime qua `asset_protocol_scope().allow_directory` (`commands/media.rs:206-213`). CSP: `asset:`/`http://asset.localhost` trong img/media-src (`tauri.conf.json:33-34`).
  - `media://` custom scheme handler cũng được đăng ký (`lib.rs:56`) nhưng **không có consumer trong frontend** → `IMPLEMENTED BUT NOT ACTIVE`.

## 4.2 Database (SQLite)

Engine: SQLite qua `rusqlite bundled`; WAL + `foreign_keys` + `busy_timeout` (`src/db/mod.rs`); UUID v4 tự viết, `utc_iso8601_now`, transaction wrapper. Migration theo `PRAGMA user_version`, mỗi migration trong `IMMEDIATE` transaction (`src/db/migrations.rs:204-237`).

**8 migrations (v1→v8)** — bảng đầy đủ từ `migrations.rs`:

| # | Bảng | Cột chính (type) |
|---|---|---|
| v1 | `projects` | `id` TEXT PK (uuid), `name`, `source_video_path`, `status`, `created_at`, `updated_at`, `settings_json` |
| v2 | index `idx_projects_updated_at` | — |
| v3 | `jobs` | `id` TEXT PK (`job_NNNN`), `project_id` FK→projects ON DELETE CASCADE, `type`, `status`, `progress REAL DEFAULT 0`, `stage`, `error_code/message/log`, `params_json DEFAULT '{}'`, `retry_count DEFAULT 0`, `cancel_requested DEFAULT 0`, timestamps; index project_id, status |
| v4 | `cache_entries` | PK(`project_id`,`key`), `stage`, `file_name`, `size_bytes`, timestamps; index last_access, stage |
| v5 | `glossary_entries` (+`character_entries`) | PK(`project_id`,`term`) / PK(`project_id`,`name`), `translation`/`description`, `updated_at` |
| v6 | `subtitle_cues` | `id` TEXT PK (uuid), `project_id` FK, `cue_number`, `start` REAL, `end` REAL, `text`, `speaker`, `source_text`, `status` ('draft'/'translated'/'edited'/'approved'), `style_json`, `updated_at`; UNIQUE(`project_id`,`cue_number`) |
| v7 | `settings` | `key` TEXT PK, `value`, `updated_at` |
| v8 | `providers` + `provider_defaults` | providers: `id`,`name`,`provider_type`('translation'),`provider_kind`('free'/'gemini'/'local'/'mock'),`enabled`(INT 1),`base_url`,`model`,`config_json DEFAULT '{}'`,`capabilities_json DEFAULT '[]'`,`last_test_status`,`last_test_at`,timestamps. provider_defaults: PK(`capability`), FK→providers |
| v8 seed | 4 providers | `free` (capabilities `["translation","stt"]`, base_url `http://127.0.0.1:8080`), `gemini` (model `gemini-flash-lite-latest`, `["translation"]`), `local` (base_url `http://127.0.0.1:8080`), `mock`; defaults: translation→free, stt→free, tts→free (`migrations.rs:182-199`) |

**Repositories** (`src/db/repo/`): `project.rs` (CRUD + `find_by_source_path` dedup), `job.rs` (state machine `can_transition`, monotonic `job_NNNN`, resume `queued`/`running` sau crash), `subtitle.rs`, `glossary.rs` (fingerprint FNV-1a), `characters.rs`.

## 4.3 Services

| Service | Trách nhiệm |
|---|---|
| `ProjectService` (`project_service.rs`) | CRUD + thư mục `projects/{uuid}/{video,cache,output}`; id UUID chống path traversal; rollback dir khi insert fail |
| `JobService` (`job_service.rs`) | Orchestrator FIFO (1 worker chạy 1 job); state machine guard; cancel (queued→cancelled, running→flag); retry transient 1s/5s/30s max 3; resume sau crash; emit `job:status`/`job:log` |
| `PipelineRunner` (`pipeline_runner.rs`) | Xem §5 |
| `CacheService` (`cache_service.rs`) | Content-addressed (`audio:/stt:/tr:/render:` + SHA-256 streaming, mirror Python `worker/src/services/cache.py:10-18`); quota LRU 10 GB default (`settings_service.rs:25`); cascade invalidation; crash-safe rename + sweep `*.tmp-*` |
| `WorkerManager` (`worker_manager.rs`) | §4.4 |
| `WorkerClient` (`worker_client.rs`) | §4.5 |
| `HardwareProbe` (`hardware_probe.rs`) | nvidia-smi → WMI → `ffmpeg -encoders`; timeout 8s/probe; cached |
| `ProviderService` (`provider_service.rs`) | Registry providers + defaults theo capability; FREE bất biến (không xóa/disable/đổi kind); xóa default → heal về FREE; `resolve_translation` cần enabled+capability |
| `SettingsService` (`settings_service.rs`) | Whitelist 17 key (`settings_service.rs:28-46`), validate enum/range; defaults (`settings_service.rs:49-69`): `ai.model=large-v3`, `ai.device=auto`, `ai.preset=balanced`, `gpu.override=auto`, `api.gemini.model=gemini-flash-lite-latest`, `api.local.base_url=http://127.0.0.1:8080`, `cache.quota_bytes=10GB`, `privacy.mode=local`, `tts.engine=edge`, `tts.voice=vi-VN-HoaiMyNeural`, `automation.chunked=false`, `automation.chunk_duration=30`, `chunk_overlap=2`, `chunk_concurrency=4`, `chunk_retries=2` |
| `SubtitleService` (`subtitle_service.rs`) | `replace_project` atomic + `update_cue` patch validate |
| `DictionaryService` (`dictionary_service.rs`) | Glossary (term lowercase) + characters, fingerprint |
| `SecretStore` (`security/secret_store.rs`) | §7 |
| `JobEventSink` (`lib.rs:39-46`) | Emit `job:status`/`job:log` lên frontend |

## 4.4 Worker process lifecycle (`worker_manager.rs`)

- Config: `python` (None → `WORKER_PYTHON` env → PATH `python`), `source_dir`, `resource_dir` (optional; bundled FFmpeg/llama), `worker_bin` (bundled worker override **đã hỗ trợ nhưng không sinh exe nữa**) (`worker_manager.rs:68-96`).
- `spawn_worker` (`worker_manager.rs:709-840`): nếu có `worker_bin` → spawn thẳng; còn lại `python -m src.main --port <ephemeral>` chạy từ source tree (`worker_manager.rs:728-737`). Nếu `resource_dir` cấu hình: set `FFMPEG_BIN`/`FFPROBE_BIN`/`LLAMA_SERVER_BIN` (chỉ khi file tồn tại) (`worker_manager.rs:747-765`).
- Port: `pick_ephemeral_port()` (`worker_manager.rs:711`).
- Auth: token 64 hex chars, handshake `READY <token>` qua stdin pipe; compare constant-time; luôn redact token khỏi log (`worker_manager.rs:582-597,888`).
- Restart có giới hạn; graceful shutdown qua `SHUTDOWN`; supervisor thread (`worker_manager.rs:273-289`).

## 4.5 Worker HTTP client (`worker_client.rs`)

- HTTP/1.1 tự viết trên `TcpStream`, loopback `127.0.0.1` (`worker_client.rs:18`).
- Pipeline timeout 4h (`worker_client.rs:40`), cap response 16 MiB, bearer token chỉ trong `Authorization` header.
- Endpoints gọi từ Rust (cùng định dạng worker): `/health`, `/v1/audio/extract`, `/v1/stt/transcribe`, `/v1/translate`, `/v1/subtitle`, `/v1/render`, `/v1/export/*`, `/v1/automation/chunked`, `/v1/automation/finalize`, `/v1/providers/test`, `/v1/tts/*`, `/v1/logo/remove`, `/v1/audio/process`, `/v1/models/*`, `/v1/progress/{job_id}`, `/v1/jobs/{job_id}/cancel` (mirrors `worker_client.rs:613-686`).

---

# 5. PIPELINE (orchestration Rust + execution Worker)

## 5.1 Rust: JobService + PipelineRunner

`PipelineRunner::run` dispatch theo `job_type` (`pipeline_runner.rs:1564-1573`): 8 loại — `Transcribe | Translate | Subtitle | Tts | Render | Logo | Audio | Chunk`.

- Mỗi stage = 1 HTTP call riêng tới worker, rồi **verify artifact output tồn tại trên đĩa** trước khi báo thành công (guard `E_ARTIFACT_MISSING`; không bao giờ báo success nếu file thiếu) (`pipeline_runner.rs:409,515,516`).
- Live progress poll 500ms; cancel abort timeout 10s; stage-level retry 3 lần, backoff 2s→4s, allowlist `E_TTS_FAILED|E_API_ERROR|E_API_RATE_LIMIT` (`pipeline_runner.rs:278`).
- **Subtitle merge bảo toàn user edits**: `merge_subtitle_cues(existing, fresh)` match theo timing ±0.5s, mỗi row dùng 1 lần; row `edited`/`approved` hoặc text không đổi → giữ user version (`pipeline_runner.rs:1586-1617`).
- Artifact scheme (một nguồn sự thật): `project_dir/{video,cache,output}`, hằng số `ARTIFACT_*` (`pipeline_runner.rs:95-125`).

### 5.1.1 Chunk stage (Rust orchestration) (`pipeline_runner.rs:1216-1556`)

`run_chunk` (1 job chạy cả chuỗi):
1. Extract audio → verify WAV (`:1234-1262`).
2. Đọc settings tunable: `automation.chunk_duration` (default 30), `chunk_overlap` (2), `chunk_concurrency` (4), `chunk_retries` (2) (`:1333-1337`).
3. `POST /v1/automation/chunked` (`ChunkedAutomationRequest`) trong retry wrapper, progress 0.05→0.9 (`:1358-1401`).
4. Nếu `failed_chunks` non-empty → job fail (`:1408`).
5. Verify merged artifacts `cache/subtitle.ass`, `cache/voice_track.wav` (nếu dub) (`:1435-1446`).
6. Render final video (`POST /v1/render`) → verify output file (`:1469-1510`).
7. `POST /v1/automation/finalize` → final validation + verify + cleanup (`:1522-1550`).

## 5.2 Worker: HTTP surface

20 endpoints (đầy đủ từ `worker/src/api/routes.py` + `pipeline.py`):

| Method | Path | File:line |
|---|---|---|
| GET | `/health` | `routes.py:63` |
| POST | `/v1/stt/transcribe` | `routes.py:87` |
| POST | `/v1/export/video` | `routes.py:175` |
| POST | `/v1/export/subtitles` | `routes.py:204` |
| POST | `/v1/audio/extract` | `pipeline.py:409` |
| POST | `/v1/translate` | `pipeline.py:436` |
| POST | `/v1/providers/test` | `pipeline.py:485` |
| POST | `/v1/subtitle` | `pipeline.py:581` |
| POST | `/v1/tts/synthesize` | `pipeline.py:628` |
| GET | `/v1/tts/voices` | `pipeline.py:673` |
| POST | `/v1/tts/preview` | `pipeline.py:727` |
| POST | `/v1/render` | `pipeline.py:769` |
| POST | `/v1/automation/chunked` | `pipeline.py:876` |
| POST | `/v1/automation/finalize` | `pipeline.py:927` |
| POST | `/v1/logo/remove` | `pipeline.py:973` |
| POST | `/v1/audio/process` | `pipeline.py:1006` |
| GET | `/v1/models/catalog` | `pipeline.py:1050` |
| POST | `/v1/models/download` | `pipeline.py:1081` |
| POST | `/v1/jobs/{id}/cancel` | `pipeline.py:1178` |
| GET | `/v1/progress/{job_id}` | `pipeline.py:1187` |

Auth: bearer token (session sidecar → `WORKER_AUTH_TOKEN` → `"dev-placeholder-token"`), `secrets.compare_digest` (`routes.py:22-60`). Router `pipeline.py` dùng dependency `require_bearer` (`pipeline.py:48`).

## 5.3 Worker: pipeline flow

### 5.3a Classic (stage-per-call, Rust chỉ huy từng job)

1. **audio/extract** → WAV 16k mono PCM s16le (`audio_service.py:55-66`).
2. **stt/transcribe** → Transcript (faster-whisper).
3. **translate** → Translation blocks (provider abstraction + TranslationMemory).
4. **subtitle** → ASS/SRT cues.
5. **tts** (nếu dub) → voice_track.wav.
6. **render** → MP4 burn-in (+ voicetrack/watermark).
7. **logo/remove** hoặc **audio/process** → stage phụ.
8. **export/video|subtitles** → copy ra đích; video có QC ffprobe.

### 5.3b Chunked automation (`chunk_service.py`)

- `run_chunked_pipeline` (`chunk_service.py:1045-1279`): probe video → `build_chunks` → `ChunkScheduler` (bounded pool `ThreadPoolExecutor`, max_concurrency default 4, retry default 2) (`chunk_service.py:130-176,320-417`).
- Mỗi chunk (`process_one_chunk` `:862-1037`): `slice_audio` (16k mono) → STT (`E_STT_NO_SPEECH` → chunk silent hợp lệ) → shift timestamp + `clamp_to_logical` (bỏ overlap) → translate → (nếu dub) TTS local.
- Assembly (`:1151-1250`): `merge_segments` (sort + dedupe + renumber `seg_N`), `assemble_translations` (pair theo identity `(chunk_id, src_idx)`, FIX #1), subtitle (=SubtitleService), `concat_voice_tracks` (chunk thiếu → silence).
- Artifacts vào `project_dir/cache/`: `transcript.json`, `translation.json`, `subtitle.srt/ass`, `voice_track.wav` (nếu dub), `chunk_manifest_{job_id}.json`. Temp: `project_dir/temp/{job_id}/chunks/chunk_XXXX/`.
- Validation chain: per-chunk → order → timeline → final (`final_validation` ffprobe; `:694-724`) → `verify_output` size ổn định (`:732-749`) → `CleanupManager` xoá temp chỉ khi cả 2 pass (`:757-782`).

## 5.4 Chunk parameters

- Logical chunks **30s** (UI cho 20/30/45/60s — `ALLOWED_CHUNK_DURATIONS`, `chunk_service.py:52`), **overlap 2s** dành riêng context (`chunk_service.py:49-59`). Ví dụ 60s video: `chunk_0001 [0,32] logical[0,30)`, `chunk_0002 [28,60] logical[30,60)` (`chunk_service.py:140-146`).
- Tunable qua settings: `automation.chunk_duration|overlap|concurrency|retries` (`settings_service.rs:63-67,113-153`; `pipeline_runner.rs:1333-1337`).
- Rust request `ChunkedAutomationRequest` mirror worker (`worker_client.rs:613-649`).

---

# 6. AI SERVICES (Worker)

## 6.1 STT (`stt_service.py`)

- Backend chính: **faster-whisper** (ctranslate2). `WhisperModel(..., local_files_only=True)` trước, fallback cho download (`stt_service.py:221-231`); model cache theo `(model, device, compute_type)` (`_WHISPER_MODEL_CACHE` `:56-63`).
- Model id default `"large-v3"` (`routes.py:79`, `stt_service.py:313`); tier `large-v3/turbo/small/base/tiny` + VRAM map (`stt_service.py:69-75`); `guard_model_tier` downgrade theo VRAM (`:162-176`).
- Compute: `int8` CPU, `int8_float16` CUDA (`stt_service.py:144-146`); device `resolve_device("auto")` (torch.cuda → ctranslate2 count) (`:118-141`); CUDA-runtime-lib fail (`cublas/cudnn/...`) → **retry 1 lần trên CPU** (`:420-437`).
- VAD: Silero built-in `vad_filter=True` (`:393`), beam_size=5 (`:394`).
- Backend phụ **whisper-cpp** (`_transcribe_whisper_cpp` `:447-528`), 3 mitigation (beam ≤6, `--no-flash-attn` AMD/Intel, init lock) — **không route nào kích hoạt** → `IMPLEMENTED BUT NOT ACTIVE`.
- Lỗi: `E_STT_MODEL_UNAVAILABLE`, `E_STT_FAILED`, `E_STT_NO_SPEECH` (`:47-49`).

## 6.2 Translation (`translation_service.py` + providers)

- **TranslationMemory**: key `(source_hash, target_language, glossary_ver, rules_ver, model)` (`:40-107`), `rules_version` = sha256(sorted rules) (`:45-55`); persist JSON (`:112-145`).
- `translate_segments`: chunk (ContextEngine) → block trong TM thì assemble từ cache, else `QualityGate.run(provider, block)` (`:245-265`); `_assemble` bảo toàn idx/segment_id (FIX #1, `:162-189`).
- ContextEngine (`context_service.py`): block 5-10 cues, cắt ở ranh giới speaker sau đạt min; overlap đọc tối đa 2 block trước + 1 sau; token budget guard 70% window (window 1M token); prompt template tiếng Việt (`:199-232`).
- **Provider interface** (`providers/base.py:72-82`): `TranslationProvider` Protocol — `translate_block`, `estimate_cost`, `health`.
- Factory (`pipeline.py:366-401`): `"mock"` → MockProvider; `"gemini"` → GeminiProvider; `"local"` và `"free"` → LocalLLMProvider (yêu cầu `server_url`/`base_url` hoặc `model_path`, thiếu → `E_PROVIDER_UNAVAILABLE`, **không silent fake fallback**).

### Provider implementations

| Provider | Trạng thái | Chi tiết |
|---|---|---|
| `mock_provider.py` | ACTIVE (default test) | Map deterministic `{target_language: {source: translated}}`, confidence 1.0; `fail_mode` cho test lỗi |
| `gemini_provider.py` | ACTIVE | Model default `gemini-flash-lite-latest` (`:49-51`); dùng **google-genai SDK** (`genai.Client`) generate_content với `response_schema` mirror `schemas/translation.schema.json` (`:61-87,195-223`); retry 3 backoff (1,2,4)s; `E_API_AUTH` 401/403, retryable 409/429/5xx; repair JSON fence; `estimate_cost` chars/1000*0.0001 USD |
| `local_llm_provider.py` | ACTIVE (local/cloud qua base_url hoặc llama-server binary) | Cho `kind=local`; llama-server spawn (allowlist `{"llama-server","llama-server.exe"}`, env `LLAMA_SERVER_BIN`), port ngẫu nhiên 127.0.0.1, `--n-gpu-layers` theo VRAM (`:99-103`); hoặc gọi `POST {base_url}/v1/chat/completions` model `"local"` (`:306-319`); `GET {base_url}/health`; Bearer từ settings; retry 3; `E_LOCAL_LLM_START`/`E_LOCAL_LLM_NOT_FOUND`/`E_API_*` |

## 6.3 TTS (`tts_service.py`)

- Engines: `edge` (Microsoft Edge neural, **cloud**, default) + `piper` (**local**) (`:43-45`); `available_engines()` theo package importable (`:269-284`).
- Voice list: **hard-coded trong worker** (single source of truth, serve qua `/v1/tts/voices`): `EDGE_VOICES` 35 voice (2 vi, 8 zh-CN, 9 en-US, 5 en-GB, 2 ja, 3 ko, 2 fr, 2 de, 2 es) (`:59-104`); `PIPER_VOICES` 2 (`vi_VN-vais1000-medium`, `zh_CN-huayan-medium`) (`:106-109`); `VOICE_META` gender/age (`:116-164`).
- Default voice theo ngôn ngữ `_DEFAULT_VOICE` (`:212-221`); `_DEFAULT_VOICE_FALLBACK = {}` — ngôn ngữ không hỗ trợ → `E_TTS_UNAVAILABLE`, không fallback sai giọng (`:224,292-313`).
- Edge synthesis: `edge_tts.Communicate(...).save(mp3)` → ffmpeg normalize 44.1k mono 16-bit wav (`:360-392`); retry `_EDGE_MAX_ATTEMPTS=3` delay 1.5s cho `NoAudioReceived` (`:351-358`).
- Piper model: HF `rhasspy/piper-voices` (ONNX+JSON) qua `huggingface_hub.hf_hub_download` (`:192-202,395-416`).
- Chunked TTS / assembly (`synthesize_cues` `:480-556`): mỗi cue → `cue_XXXXX.wav`; speech vượt cửa sổ → `atempo` speed-fit **tối đa 1.5x** (`:54,472-477`); `_assemble_track` chèn vào track câm full-duration (`:559-579`). Output `voice_track.wav` + `tts_meta.json`.

## 6.4 Subtitle generation (`subtitle_service.py`)

`SubtitleEngine.generate` = **merge cùng speaker → wrap line-break → CPS checks → timing padding → serialize** (`:340-395`):

- Merge: adjacent cue cùng speaker, gap ≤0.25s, text ≤ `2 × max_chars_per_line` (`:45-46,242-285`).
- Param: `DEFAULT_MIN_GAP_SECONDS=0.05`, `DEFAULT_MIN_DURATION_SECONDS=0.2` (`:43-46`); CPS vượt `max_cps` → kéo dài duration (ceil 10ms) hoặc warn (`:444-464`); enforce min gap + không overlap.
- Style presets (`:63-72`): standalone default font **Arial** 44, `max_chars_per_line=42`, `max_cps=18`; CJK presets (Microsoft YaHei, 24 chars/14 cps) theo ngôn ngữ. `max_chars_per_line` **luôn từ style, không hard-code** (`:5-6`).
- LineBreakPolicy theo language, cluster-aware, cắt theo word (`:139-209`); `_measure` injectable (font-metric thật).

## 6.5 Audio extract & processing

- **extract**: `-vn -ac 1 -ar 16000 -c:a pcm_s16le`, `-progress pipe:1` cho tiến độ (`audio_service.py:55-100`); lỗi `E_FFMPEG_NOT_FOUND`/`E_FFMPEG_FAILED`.
- **process** (`audio_process_service.py`, 3 mode, `:33`):
  - `vocal_removal`: `pan=stereo|c0=0.5*c0-0.5*c1|...` (cancel kênh giữa) — **không ML stem splitter** ("honest MVP", `:3-11`).
  - `normalize`: `loudnorm=I=-16:TP=-1.5:LRA=11` (`:40`).
  - `denoise`: `afftdn=nf=-25` (`:41`).
  - Output stereo 44.1k PCM s16le (`:65-80`).

## 6.6 Logo removal (`logo_service.py`)

Filter `delogo=x:y:w:h` (`:50-77`), enable window `between(t,start,end)`, clamp vùng giữ 1px margin (`:80-102`); output libx264 medium crf18 + audio copy (`:69-75`).

## 6.7 Media probe (`media_service.py`)

`ffprobe -print_format json -show_format -show_streams` argument-array (`:334-363`); allowlist `{"ffprobe","ffprobe.exe"}` (`:44,72-102`); timeout 30s (`:37`); retry `-err_detect ignore_err` (`:115-124`); normalize rotation + FPS rational (`:218-266`); lỗi `E_VIDEO_INVALID`/`E_VIDEO_CORRUPTED`/`E_FFMPEG_NOT_FOUND`.

## 6.8 Render (`render_service.py`)

- Input: video + (subtitle | cues → rebuild ASS `:786-798`) + voice_track/audio_track + watermark. Output MP4. `encoder`/`preset`/`crf` tham số hoá (`build_drawtext_filter`; watermark `:559-588`).
- Watermark: text (drawtext, escaping chuẩn `:116`) hoặc image; `font_file` copy vào workdir, không path escape (`:757-792`); `watermark_fingerprint` dùng cache (`:666-711`).
- Codec: output codec cho phép `h264/hevc/av1/vp9/mpeg4` (`:101`); audio AAC trừ khi `-c copy` (`test_render_audio_codec.py`); auto pick encoder theo HW capabilities.
- Ưu tiên: HW encoders (nvenc/qsv/amf) detect từ `ffmpeg -encoders`; fallback `libx264` (`hardware.py:48-53`).

## 6.9 Quality gate (`quality_service.py`)

- Validation 1:1 (count, unique idx, segment_id khớp, text non-empty) (`:91-139`); language heuristic vi/zh/en (`:52-54,82-88`); CPS threshold default 20 (`:50,155-168`).
- Retry `MAX_RETRIES=3`, backoff (1,5,30)s, transient `{E_API_RATE_LIMIT, E_API_ERROR, E_PROVIDER_UNAVAILABLE}` (`:44-47`); giữ kết quả "tốt nhất" (nhiều items nhất, `:243-256`).

## 6.10 FFmpeg-safe execution (`core/ffmpeg.py`, `core/job.py`)

- Binary resolve qua allowlist + `FFMPEG_BIN` env; argument-array (không shell); string cấm `;,|,&,\n,\0` trong path (`core/ffmpeg.py:44-47,86-110`).
- Process reaper: `_kill_tree` = `taskkill /T /F` (Windows) hoặc SIGTERM→SIGKILL grace 5s (`core/job.py:34,186-240`); `CancellationToken` progress/stage/message (`core/job.py:53-96`).

## 6.11 CUDA libs (`core/cuda_libs.py`)

`os.add_dll_directory` + prepend PATH `site-packages/nvidia/*/bin` và package dir ctranslate2 (`:25-69`) — để pip `[cuda]` wheels hoạt động khi import faster-whisper.

---

# 7. SECURITY & SECRETS

- **Secrets**: API key của provider nằm trong **OS Credential Manager (Windows) qua `keyring`** — KHÔNG bao giờ vào SQLite; không có fallback file/custom-crypto (FIX #8, fail-safe rõ ràng) (`src/security/secret_store.rs`). Frontend chỉ thấy masked `AIz****wxyz` (`secret_store.rs:197-211`).
- Không log secret: token worker redact; key không có `Debug` impl; error envelope worker không chứa stack/path.
- `provider.save` (Settings `Save & Test`) chỉ lưu key khi test sống thành công.
- **Worker auth**: bearer token 64-hex, trao đổi qua stdin, compare constant-time, chỉ trong header `Authorization`; worker chỉ bind **loopback** (`main.py:35`).
- **Asset protocol scoped runtime** theo project dirs (`commands/media.rs:206-213`); CSP strict (`tauri.conf.json:29-41`); capabilities deny-by-default chỉ `core:default` + `dialog:default` (`src-tauri/capabilities/default.json`).
- `.gitignore` blocks `.env`, `*.key/pfx/pem/p12` (`.gitignore:11-18`); `.gitleaks.toml` allowlist 1 fixture `AIzaSy-secret-key-1234` (`src/api/settings.test.ts`).
- Frontend không map `.env` secrets — `src/lib/env.ts` chỉ detect Tauri env (`isTauri()`).

---

# 8. FRONTEND LAYER

## 8.1 Routing (không router thư viện)

- State-based trong `App.tsx`: `useState<NavKey>("home")`; switch `home | automation | custom | tools | settings` (`App.tsx:16,52-93`). `NavKey`/`NAV_AREAS` (`src/lib/nav.ts:12-20`).
- Automation và Custom đều render **cùng `StudioWorkspace`** với prop `mode` (`App.tsx:61-77`).
- Provider tree: `JobsProvider > ProvidersProvider > VoicesProvider > (TopBar + view) + JobFailBanner` (`App.tsx:41-95`).

## 8.2 API bridge & events

- `src/api/invoke.ts:17-29` — `safeInvoke<T>` bọc `invoke()`; ngoài Tauri ném Error rõ ràng (`isTauri()` = `window.__TAURI_INTERNALS__`, `src/lib/env.ts:6-8`).
- Module theo nhóm (đầy đủ: project/job/export/media/models/pipeline/provider/settings/subtitle/system/worker/voices/dictionary/dialog/bridge). Toàn bộ command strings khớp backend (đối chiếu 52 matches).
- Events listen: `job:status`, `job:log` (`src/api/events.ts:14-30`), `models:download-progress` (`:36-45`); no-op ngoài Tauri.
- Polling: jobs store poll 3s (`stores/jobs.tsx:31`) + listen merge; worker store poll 3s module-level (`stores/worker.ts:17`); `ensureWorkerReady` poll 500ms tối đa 15s + auto-restart (`StudioWorkspace.tsx:759-785`).

## 8.3 Studio workspace flow

Import (dialog `mp4/mkv/mov/avi/webm/m4v` hoặc drag-drop) → `findBySourceVideo` → mở hoặc `create` → chọn options → `handleAutomate` (check worker ready → check provider key → steps → submit stage đầu, stage sau submit khi trước `succeeded`) (`StudioWorkspace.tsx:749-757,787-832`).

**Session persist** (`src/workspace/session.ts`): localStorage per-project `studio.plan.<pid>`, `studio.options.<pid>`, `studio.customTools.<pid>` (`session.ts:30-32`); hydrate 1 lần per project, guard `hydratedProjectId` chống clobber (`StudioWorkspace.tsx:207-286`); plan chỉ save khi run bắt đầu (`:282-286`). Migration: voice + dubAudio=false → tự bật dub (`:225`).

## 8.4 Automation options

Options (`StudioWorkspace.tsx:132-144`, `workspace/types.ts:79-106`): `sourceLanguage`, `targetLanguage` (vi/en/zh/ja/ko, default vi), `provider` (từ `providersFor("translation")`), `burnSubtitles` (default true), `dubAudio` + `voice` + `ttsEngine`, `chunked` (default false), `watermark`, `logoRemoval` (x/y/w/h), `overlay` (subtitleStyle).

Pipeline steps builder (`src/pages/Automation/automation.ts:296-307`): `chunked` → `["chunk"]`; else `[transcribe,translate,subtitle]` + `tts` (nếu dub) + `logo` (nếu logoRemoval) + `render`. `buildStageParams` map từng stage → params `job.submit` (`:126-201`). Progress chia đều mỗi stage (`:406-423`).

## 8.5 Subtitle editor (2 bản)

- **Tools standalone** `SubtitleEditorView.tsx`: double-click edit, confirm-delete, undo stack cap 50, save debounce 600ms (`:341-351`), Ctrl+S flush (`:375-384`), row windowed/virtualized (`:155-301`). Fallback `EMPTY_PROJECT` (`:6`).
- **Trong StudioWorkspace**: cue editing + undo/redo toàn cục (`workspace/cueHistory.ts`, cap 50, diffCues patch start/end/text); Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y (trừ khi focus input) (`StudioWorkspace.tsx:662-684`); timeline drag cue block (`Timeline.tsx:63-82`); backend `merge_subtitle_cues` bảo toàn edits.

## 8.6 Export

- Workspace: TopBar Export (khi `pipeline.canExport`), native dir picker, `exportVideo(runQc:true)` (`StudioWorkspace.tsx:872-888`).
- Tools `ExportView.tsx`: video (QC verdict `passed/issues/warnings`) + subtitle format **srt/vtt/ass** (`:11`); ASS không chuyển đổi được (`api/export.ts:16`); input path tay, auto-suffix `(1)` nếu trùng tên (`:80`).

## 8.7 Settings

`Settings/index.tsx` sidebar `SETTINGS_NAV` (`:59-67`): **General, Providers, AI, Video, Subtitle, Storage, About**. Tải `settings.get_all`, save `settings.set`, remap legacy `voice`/`processing` → `ai` (`:74-77`); test connection `ping`; restart worker.

Sections (`sections.tsx`): General/Video/Subtitle/Security là **InfoRow tĩnh** ("Custom styling — Not available in this build" `:207`); VoiceSection dữ liệu thật từ `settings.voices` (`:118-190`); ProcessingSection (worker state/restart, `ai.model`, `ai.device`, `gpu.override`, `ai.preset`, cache quota) (`:214-356`); PrivacySection (`privacy.mode`, telemetry) (`:360-396`).

`ProvidersPanel.tsx`: CRUD provider (free lock khi edit `:541`), `Save & Test`, default select, `LocalModelManager` (`models.catalog`/`list_local`/`download` + progress) (`:125-295`). Capabilities chú thích "only Translation is active now" (`:576-578`).

## 8.8 Dictionary

`Dictionary/index.tsx`: Glossary (term+translation upsert/delete) + Characters (name+description) (`:31-73`); nhập Project ID tay + Load (`:84-101`), fallback `EMPTY_PROJECT` (`:13`). `glossaryFingerprint` export nhưng **không dùng** → `IMPLEMENTED BUT NOT ACTIVE`.

## 8.9 State management (không zustand)

- Context: `JobsProvider` (jobs/projects/activeJob, poll 3s + listen), `ProvidersProvider` (list + `providersFor`/`defaultFor`, fallback registry `FALLBACK_PROVIDERS`), `VoicesProvider` (engines/voices/favorites/recent/preview cache single-flight).
- Module singleton + `useSyncExternalStore`: `useWorker` (worker info + hardware, 1 polling loop 3s dùng chung), `useStudioStatus` (workspace push phase/progress/canExport/cancel/undo/redo lên TopBar).

## 8.10 Voice library

Data từ worker registry (`settings.voices` → engines edge/piper + voices + defaults), helpers `src/lib/voiceLibrary.ts` derivate nhãn/alias từ data thật (không hard-code). Favorites/recent localStorage `aivs.voice.*` (`stores/voices.tsx:25-26`). UI `VoicePicker`/`VoicePickerButton` với preview thật + cache single-flight.

---

# 9. LOGGING & PROGRESS

- **Worker**: structured JSON 1 dòng ra stdout `{ts, level, logger, msg, exc_info?}` ISO-8601 UTC (`worker/src/core/logging.py:9-30`).
- **Rust**: `log` crate → Tauri; worker stdout/stderr read-into-channel, log tiền tố `[worker:stdout]` (`worker_manager.rs:612-615`).
- **Progress chuỗi job**: worker `CancellationToken` (`core/job.py:53-96`), `/v1/progress/{job_id}` poll; Rust poll 500ms và map phần trăm zero-based 0..1 mỗi stage (`pipeline_runner.rs:278,283`).
- **Chunked**: progress từng chunk trong manifest; tiến độ stage chunk 0.05→0.9 (`pipeline_runner.rs:1357,1399`).
- **Frontend**: LiveLog console (collapse, auto-scroll, max-logs, ETA) (`src/pages/Automation/LiveLog.tsx`), log filter theo level (`logHelpers.ts`).

---

# 10. CONFIGURATION & SETTINGS

- App settings SQLite `settings` table, 17 whitelisted keys, validate enum/range (`settings_service.rs:28-46`); `get_all` trả defaults cho key chưa ghi (`:8-10,49-69`).
- Provider config trong DB (providers/providers_defaults), secrets trong OS vault (§7).
- Worker tunable (chunk params) đọc từ Rust settings rồi đẩy vào request `ChunkedAutomationRequest` (`pipeline_runner.rs:1333-1337`).
- `tauri.conf.json`: window 1360×840, CSP, asset protocol. `plugins: {}` — **không updater** (`:57`). `bundle.active: false` (`:59`). `resources` map `../vendor/ffmpeg` + `../vendor/llama` (`:69-72`) — bundle inactive nên dormant; `vendor/llama` không tồn tại.

---

# 11. AUTHENTICATION / SECURITY BOUNDARIES

- Frontend→Rust: Tauri IPC, capabilities deny-by-default (`capabilities/default.json`).
- Rust→Worker: loopback + bearer token 64-hex (sidecar stdin handshake), token rotate khi restart (`worker_manager.rs`); timeout 4h; response cap 16 MiB.
- Hệ thống: process allowedlist (ffmpeg/ffprobe/whisper-cli/llama-server + `.exe`), argument-array, ký tự cấm; asset protocol scope runtime.
- Không có auth người dùng / OAuth / account. `NOT IMPLEMENTED` (ngoài scope MVP).

---

# 12. TEST STRUCTURE

| Layer | Config | Nội dung |
|---|---|---|
| Frontend | Vitest `node` env, `renderToStaticMarkup` | 28 file test (`src/**/*.test.{ts,tsx}`): api bridge/job/project/media/subtitle/settings/export/dictionary, stores, pages, components, workspace. `npm run test` |
| Rust | inline `#[cfg(test)]`; `cargo test` | 100+ test: migrations, db, repo, services (cache 18+, pipeline_runner 19+, worker_manager, worker_client, secret_store 7, project_service 9, provider_service 7, hardware_probe 8), contract_tests (5, validate `schemas/examples/valid`), commands |
| Worker | `pytest` (từ `worker/`) — không có config pytest trong repo, cần `pip install pytest` | Unit (10 file): schema_examples (validate mọi example), chunk_service (29 tests), render_ass, render_audio_codec, render_fixes, logo_audio_service, tts_service_retry, tts_voice_library, translation_memory, ffmpeg_progress. Integration (chạy script trực tiếp, 4 file): `e2e_pipeline.py` (pipeline thật loopback+ffprobe), `e2e_chunked.py`, `e2e_providers.py` (OpenAI-compat stub), `e2e_ui.py` (CDP WebView2) |
| CI | `.github/workflows/ci.yml` | worker job **chỉ smoke import** (`python -c "import src.main"`), không pytest; `PARTIAL` |

---

# 13. TRẠNG THÁI & LƯU Ý (từ code thực tế)

## IMPLEMENTED BUT NOT ACTIVE
- whisper-cpp STT backend (`stt_service.py:447-528`) — không route kích hoạt.
- `media://` custom scheme handler (`lib.rs:56`) — frontend dùng `asset://` (`api/media.ts:26`).
- `glossaryFingerprint`, `project.open`, `providers.get`, `secrets.*` API, `groupVoices` — export nhưng không consumer runtime.
- Form `components/WatermarkConfig.tsx` — không có UI control nào render ra; nhưng wire vào render job **có** qua `watermarkToWire` (`automation.ts:166-175`), state `watermark` trong workspace (`StudioWorkspace.tsx:142`) được persist trong session.
- `WorkflowController` no-op (`StudioWorkspace.tsx:1004-1013`) — tàn dư pipeline editor cũ.
- Model stack `model_registry|download|cache|verifier` — tất cả entry `models/manifest.json` **UNPINNED** (`expected_size_bytes:0, checksum:""`) → `resolve()` trả rỗng; nhưng `LocalModelManager` (UI) dùng `models.catalog`/`download` (GGUF catalog hard-coded 1 entry Qwen2.5-3B, `pipeline.py:1038-1047`). `PARTIAL` khi kể cả cụm này là một phần hệ thống kích hoạt.

## PARTIAL
- Worker pytest không có config trong repo; CI worker không chạy pytest.
- Settings Video/Subtitle/General/Security sections là static InfoRow ("Not available in this build").
- STT/TTS capabilities providers: stored, only Translation active.
- Worker `PIPER_VOICES` = 2; piper cần download model ngoài.
- docs/AI_PIPELINE.md ghi translation default `gemini`, nhưng seed DB v8 default translation = `free` (`migrations.rs:196-199`) — UI resolve default qua `defaultFor("translation")`; giá trị thật khi fresh install là `free`. `NOT VERIFIED` đối với hành vi UI cuối khi user đổi.

## NOT IMPLEMENTED
- Exe/installer/updater packaging (ngoài scope per DEVELOPMENT.md; `bundle.active:false`).
- OCR service (`worker/src/services/providers/ocr/` chỉ có `.gitkeep`).
- Voice cloning / separation ML / timeline / billing / cloud backend (ngoài MVP scope per AGENTS.md).

## NOT VERIFIED
- Hành vi endpoint `/v1/export/*` khi `run_qc=true` tại tầng frontend ExportView (QC verdict render) — đã có code worker QC, UI workspace truyền `runQc:true`.

---

# 14. TÀI LIỆU HIỆN CÓ & MỨC KHỚP CODE

| File | Khớp code? |
|---|---|
| `DEVELOPMENT.md` | Khớp (local-only, bundle inactive, WORKER_PYTHON, cuda install) |
| `docs/VIDEO_PIPELINE.md`, `docs/AI_PIPELINE.md`, `docs/CHUNKED_PIPELINE_*.md` | Khớp (minor: default provider) |
| `docs/FINAL_FUNCTIONAL_AUDIT.md` | Bản ghi lịch sử (base commit `6bc5d58`, test counts outdated) |
| `DATABASE.md` | **Outdated** — ghi "Schema current version 7" nhưng code có 8 migrations (v8 providers); thiếu bảng providers/provider_defaults; trỏ `SECURITY.md` không tồn tại |
| `API.md` | **Outdated** — thiếu ~11 worker routes + ~15 IPC commands (providers.*, models.*, media.probe, worker.*, system.hardware/reveal, settings.voices/ttsPreview, project.findBySourceVideo/rename/updateSettings, job.list_all) |
| `deny.toml` | Reference README "TODO" — README không tồn tại |
| `.prettierignore` | Liệt kê doc đã không còn tồn tại (MASTER_PLAN/ARCHITECTURE_DECISION/...) |
| `AGENTS.md` | Khớp hiện trạng (no EXE, local-only) |

---

# 15. GLOSSARY (enum giá trị dùng chung)

- `JobType` (`db/repo/job.rs`): `transcribe | translate | subtitle | tts | render | logo | audio | chunk`.
- `JobStatus`: `queued | running | succeeded | failed | cancelled`.
- `provider_kind` (migration v8): `free | gemini | local | mock`.
- `provider_type`: `translation` (stt/tts reserved trong comment migration, chưa seed).
- `privacy.mode`: `local | cloud`.
- Subtitle cue `status`: `draft | translated | edited | approved`.
- Project `status`: `draft | analyzed | transcribed | translated | rendered`.
- Audio process mode: `vocal_removal | normalize | denoise`.
- Lỗi chính: `E_*` (worker envelope + Rust error codes): `E_FFMPEG_NOT_FOUND`, `E_ARTIFACT_MISSING`, `E_WORKER_NOT_READY`, `E_API_AUTH`, `E_API_RATE_LIMIT`, `E_API_ERROR`, `E_PROVIDER_UNAVAILABLE`, `E_STT_*`, `E_TTS_UNAVAILABLE`, `E_RENDER_INVALID`, `E_VIDEO_INVALID/CORRUPTED`, `E_DISK_FULL`, `E_PERMISSION_DENIED`, `E_CANCELLED`.