# TASKS.md — Danh sách task triển khai MVP

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Base:** `MASTER_PLAN.md` (sau S1-S6) + `ARCHITECTURE_DECISION.md` (FROZEN) + `IMPLEMENTATION_ROADMAP.md`.
**Phạm vi:** 30 task đầu cho MVP (Phase 1-10). Đây là tài liệu điều hành cho team developer / AI coding agent.

---

## 1. FORMAT TASK (bắt buộc cho mọi task)

```text
TASK-ID | Title
  Goal            — mục tiêu cụ thể, đo được
  Why             — lý do task tồn tại (liên kết phase/quyết định)
  Dependencies    — task phải xong trước (TASK-ID)
  Files/Modules   — file/module chịu ảnh hưởng
  Implementation  — các bước triển khai cụ thể
  Input           — đầu vào
  Output          — đầu ra / định nghĩa "xong"
  Acceptance      — tiêu chí chấp nhận (đo được)
  Test Cases      — các test bắt buộc
  Potential Problems — rủi ro + hướng xử lý
  DoD             — definition of done riêng của task
```

---

## 2. DEPENDENCY GRAPH (rút gọn)

```text
001 (repo) ──┬── 002 (CI)
             ├── 003 (shell) ── 004 (IPC ping)
             ├── 005 (worker) ── 006 (sidecar lifecycle)
             ├── 007 (schemas) ── 008 (SQLite+Project) ──┬── 010 (job system)
             │                                           └── 011 (cache)
              └── 016A-D (model management) ── (cần 013)

009 (media probe) ── 012 (audio extract) ── 013 (STT) ── 014 (GPU detect) ── 015 (whisper.cpp fallback)

016A (registry) ── 016B (downloader) / 016C (verifier) / 016D (cache)   [FIX #6]

017 (provider iface) ── 019 (Gemini) / 020 (local LLM)          [018 OpenAI = Post-MVP]
017 ── 021 (context engine) ── 022 (validation+retry+QC) ── 023 (glossary+TM)

023 ── 024 (subtitle engine) ── 025 (editor UI) ── 026 (preview)
024 ── 027 (render) ── 028 (watermark) ── 029 (export+QC)

010 + 011 ── 030 (settings UI + secrets + error UI)
```

**Ghi chú:** 003→004→005→006 là đường shell; 007→008→010→011 là đường data. Hai đường gặp nhau ở 012+. 017-023 là đường translation độc lập với STT (chỉ cần transcript). 024 là hợp lưu.

**FIX #10 — Translation dev ↔ runtime dependency:** Translation (019/020/021/022/023) **phát triển được song song** với STT (013) nhờ **mock transcript fixtures** (`fixtures/transcripts/*.json` — dev/test dependency, không phải runtime). **Runtime bắt buộc:** `STT → Transcript → Context Engine → Translation` — translation không bao giờ chạy trước transcript thật trong pipeline production.

---

## 3. SPRINT ĐẦU TIÊN (Sprint 0 — Foundation, mục tiêu 5-10 ngày)

Chọn các task **tạo nền móng** trước, có thể chạy song song:

| Thứ tự | Task | Lý do vào Sprint 0 |
|---|---|---|
| 1 | 001 — Khởi tạo repo | Mọi thứ bắt đầu từ đây |
| 2 | 002 — CI cơ bản | Bảo vệ chất lượng từ ngày 1 |
| 3 | 003 — Tauri 2 shell | Nền desktop |
| 4 | 005 — Python worker skeleton | Nền AI runtime |
| 5 | 007 — Schemas versioned | Single source of truth, cả 3 tầng dùng chung |
| 6 | 004 — IPC ping | Chứng minh bridge hoạt động |
| 7 | 006 — Sidecar lifecycle | Spawn/health/restart worker |
| 8 | 008 — SQLite + ProjectService | Nền dữ liệu |
| 9 | 016A-D — Model management (Registry/Downloader/Verifier/Cache; thiết kế trước, code sau 013) | Cần cho STT |

**Sprint 0 Gate:** App mở cửa sổ; worker `/health` OK; Rust spawn/kill worker; project CRUD trong SQLite; CI xanh.

---

## 4. CHI TIẾT TASK

### TASK-001 | Khởi tạo repository & foundation
- **Goal:** Repo GitHub riêng tư, cấu trúc đúng `MASTER_PLAN.md §22`, README + git + .gitignore + AGENTS.md.
- **Why:** Phase 1 — nền tảng cho mọi thứ sau; hướng dẫn AI coding agent (AGENTS.md) bắt buộc.
- **Dependencies:** —
- **Files/Modules:** `README.md`, `.gitignore`, `AGENTS.md`, `LICENSE`, toàn repo skeleton
- **Implementation:** (1) `git init` + branch `main` + protect; (2) cấu trúc thư mục theo §22; (3) README (mô tả, quickstart, link docs); (4) AGENTS.md (quy tắc: không commit secret, chạy test nào, format); (5) .gitignore (target/, node_modules/, dist/, vendor/, models/, *.key, *.pfx).
- **Input:** MASTER_PLAN §22.
- **Output:** Repo clean, có thể clone và dev ngay.
- **Acceptance:** `git status` sạch; clone mới + cài deps không lỗi; không có secret trong git history.
- **Test Cases:** `git check-ignore` cho các pattern; scan history bằng `gitleaks` (CI).
- **Potential Problems:** File lớn (video fixture) bị push → thêm .gitignore + `git lfs` nếu cần.
- **DoD:** Repo public/private theo yêu cầu, 3 file docs gốc được commit, CI bắt đầu từ TASK-002.

### TASK-002 | CI cơ bản (lint + build + test)
- **Goal:** GitHub Actions chạy lint/build/test trên `windows-latest` (và Ubuntu cho worker).
- **Why:** Phase 1 — bảo vệ chất lượng, phát hiện lỗi sớm, nền cho CI sign/release sau.
- **Dependencies:** TASK-001
- **Files/Modules:** `.github/workflows/ci.yml`
- **Implementation:** (1) job frontend: `npm ci` + `tsc --noEmit` + eslint + vitest; (2) job worker: `uv`/`pip install -e .[dev]` + `pytest` (chạy subset nhanh, không chạy `ai` marker); (3) job rust: `cargo check` + `cargo clippy -- -D warnings` + `cargo test`; (4) cargo-deny cho dependency audit (licenses). 3 job song song.
- **Input:** TASK-001.
- **Output:** CI xanh trên mỗi PR.
- **Acceptance:** PR không merge khi CI đỏ; cả 3 job pass; cargo-deny không có license bị cấm.
- **Test Cases:** CI chạy trên PR mẫu; ép 1 lỗi lint để xác nhận fail.
- **Potential Problems:** CI Windows chậm (rust compile) → dùng sccache/cache actions; timeout cao cho lần đầu.
- **DoD:** CI xanh cho repo mới, badge README, cargo-deny có whitelist license thương mại.

### TASK-003 | Tauri 2 shell + React/Vite/Tailwind
- **Goal:** App mở cửa sổ trên Windows với UI base (sidebar + main area), theme dark.
- **Why:** Phase 2 — shell desktop; WebView2 + Rust core.
- **Dependencies:** TASK-001
- **Files/Modules:** `src-tauri/` (Cargo.toml, tauri.conf.json, main.rs), `src/` (Vite React TS), `src/components/ui/*` (shadcn)
- **Implementation:** (1) `npm create tauri-app` với template react-ts; (2) thêm Tailwind + shadcn/ui; (3) layout sidebar + main (empty states); (4) CSP strict trong tauri.conf.json (chỉ `default-src 'self'` + localhost worker socket khi cần); (5) capabilities tối thiểu (core:default).
- **Input:** TASK-001.
- **Output:** `npm run tauri dev` mở cửa sổ hiển thị UI.
- **Acceptance:** Dev build chạy Win10/11; console không CSP error; WebView2 dùng được.
- **Test Cases:** Manual smoke (mở app, resize, đóng); `cargo build` không lỗi; audit CSP config.
- **Potential Problems:** shadcn cần config path alias → setup `@/*` alias trong tsconfig + vite; WebView2 thiếu → dùng edge stable runtime khi dev.
- **DoD:** Shell mở được, layout chuẩn, CSP strict, không warning console.

### TASK-004 | Bridge IPC typed đầu tiên (ping)
- **Goal:** Frontend gọi Rust qua `invoke("ping")` và nhận `"pong"`; typed wrapper `bridge.ts`.
- **Why:** Phase 2 — chứng minh IPC hoạt động; nền cho mọi command sau.
- **Dependencies:** TASK-003
- **Files/Modules:** `src/api/bridge.ts`, `src-tauri/src/commands/system.rs`, `src-tauri/src/lib.rs`
- **Implementation:** (1) command `system::ping() -> String`; (2) đăng ký trong invoke_handler; (3) `bridge.ts` wrap typed; (4) hiển thị kết quả ping trên UI Settings > About.
- **Input:** TASK-003.
- **Output:** Nút "Test connection" trả về pong + latency.
- **Acceptance:** `invoke("ping")` trả `pong`; error handling khi Rust không chạy.
- **Test Cases:** Unit Rust (command trả đúng); vitest cho bridge wrapper (mock invoke); manual.
- **Potential Problems:** Tauri v2 permission — command phải có trong capabilities; version pin `@tauri-apps/api` với core.
- **DoD:** Ping hoạt động, typed, có test.

### TASK-005 | Python worker skeleton (FastAPI + /health)
- **Goal:** Worker chạy được, `GET /health` trả `{status, version, gpu}`; cấu trúc package chuẩn.
- **Why:** Phase 2 — AI runtime; trước khi có lifecycle.
- **Dependencies:** TASK-001
- **Files/Modules:** `worker/` (pyproject.toml, requirements.txt: base/cpu/gpu/dev), `worker/src/main.py`, `api/routes.py`, `api/schemas.py`, `core/logging.py`
- **Implementation:** (1) uv/pip project; (2) FastAPI app + CORS off (loopback only) + middleware auth (placeholder token); (3) `GET /health` (import torch/faster-whisper lazy để health không nặng); (4) logging structured JSON; (5) entrypoint `uvicorn` bind `127.0.0.1`, port qua env `--port`.
- **Input:** TASK-001.
- **Output:** `python -m src.main --port <n>` → `/health` 200.
- **Acceptance:** Health trả đúng schema; không bind 0.0.0.0; chưa có gpu detect vẫn trả `gpu: null`.
- **Test Cases:** pytest + httpx: GET /health 200 + schema; auth middleware chặn request không token.
- **Potential Problems:** PyTorch import nặng → lazy import; version conflict giữa các deps → dùng requirement split.
- **DoD:** Worker chạy độc lập, health OK, có test, log structured.

### TASK-006 | Sidecar lifecycle (spawn/health/restart)
- **Goal:** Rust spawn worker, poll health, restart tối đa 3 lần, kill khi app exit; auth token qua stdin.
- **Why:** Phase 2 — process isolation là quyết định FROZEN (ARCHITECTURE_DECISION 3.1).
- **Dependencies:** TASK-005
- **Files/Modules:** `src-tauri/src/services/worker_manager.rs`, `worker_client.rs`
- **Implementation:** (1) WorkerManager: tìm binary (bundled/ dev python), spawn với `--port <random>`; (2) đọc token từ worker's stdout protocol riêng (worker in `READY <token>` sau khi bind); (3) poll `/health` 10 lần/1s → ready; (4) restart nếu crash (max 3) + log; (5) `on_window_event` close → graceful shutdown → kill sau timeout 3s; (6) port random trong range, ghi vào state.
- **Input:** TASK-005.
- **Output:** Rust `start_worker()` → health OK; `stop_worker()` sạch.
- **Acceptance:** Khởi động app → worker up trong <5s; kill worker thủ công → restart ≤3 lần rồi báo lỗi; token không xuất hiện trong argv/log.
- **Test Cases:** Integration test: spawn thật (dev), giả crash bằng cách kill process, assert restart; test token không lộ (đọc args).
- **Potential Problems:** Port conflict → retry port khác; worker zombie khi app crash → job object / `CREATE_BREAKAWAY_FROM_JOB` + kill tree; Windows process group.
- **DoD:** Lifecycle đầy đủ, token qua stdin, có integration test.

### TASK-007 | Schemas JSON versioned + Pydantic + TS types
- **Goal:** `schemas/` là single source of truth; validate 3 tầng (JSON Schema + Pydantic + TS types) nhất quán.
- **Why:** Phase 1 — mọi module dùng chung; tránh drift giữa Python/Rust/TS.
- **Dependencies:** TASK-005
- **Files/Modules:** `schemas/{transcript,translation,subtitle,job,api}.schema.json`, worker `api/schemas.py`, `src/types/api.ts`
- **Implementation:** (1) viết JSON Schema versioned theo MASTER_PLAN §24 (cập nhật model Gemini theo S1); (2) `schemas/` generator: `datamodel-code-generator` → Pydantic models cho worker; (3) TS types: giữ thủ công hoặc generate (đánh dấu auto-gen nếu phức tạp); (4) script kiểm tra tương đương (diff).
- **Input:** MASTER_PLAN §24, S1.
- **Output:** Schema files + Pydantic + TS types đồng bộ.
- **Acceptance:** `schema_version` bắt buộc; validator nhận/loại đúng mẫu hợp lệ/không hợp lệ; gen không diff khi chạy lại.
- **Test Cases:** pytest: validate từng schema; ví dụ invalid JSON bị reject; TS typecheck.
- **Potential Problems:** Generator không cover JSON Schema 100% → chấp nhận thủ công phần nhỏ, giữ 1 nguồn JSON schema làm truth.
- **DoD:** 3 tầng validate thống nhất, có test, có script gen.

### TASK-008 | SQLite setup + migrations + ProjectService
- **Goal:** SQLite WAL, migrations versioned, CRUD project đầy đủ.
- **Why:** Phase 1 — nền dữ liệu; mọi job/translation/glossary đều gắn project.
- **Dependencies:** TASK-007
- **Files/Modules:** `src-tauri/src/db/{mod,migrations}.rs`, `db/repo/project.rs`, `services/project_service.rs`, commands `project.rs`
- **Implementation:** (1) `rusqlite` + `sqlx`-style migrations (bảng từ MASTER_PLAN §17.1); (2) WAL pragma + foreign_keys ON; (3) ProjectService CRUD + tự tạo thư mục project (video, cache, output) trong user-data; (4) command IPC `project.create/open/save/delete`; (5) auto-save + updated_at.
- **Input:** MASTER_PLAN §17.1.
- **Output:** Project CRUD qua IPC, DB persist sau restart.
- **Acceptance:** Tạo/mở/lưu/xóa project; migrate từ v1 lên v2 không mất dữ liệu; WAL active; 2 app instance không ghi đè nhau.
- **Test Cases:** Unit Rust (CRUD + migration); integration: tạo project → restart app → mở lại được.
- **Potential Problems:** Migration chạy 2 lần → user_version gate; path unicode → dùng OsString, test thư mục có dấu tiếng Việt.
- **DoD:** CRUD hoạt động, migrate test pass, WAL.

### TASK-009 | Media probe (ffprobe)
- **Goal:** `ffprobe` → MediaMetadata đầy đủ; xử lý video hỏng/codec lạ.
- **Why:** Phase 3 — trước khi extract/render cần metadata đúng.
- **Dependencies:** TASK-005
- **Files/Modules:** `worker/src/services/media_service.py`
- **Implementation:** (1) gọi `ffprobe -print_format json -show_format -show_streams` bằng subprocess (arg array); (2) parse → schema (resolution, fps, duration, codec, bitrate, rotation, aspect, audio/subtitle streams); (3) lỗi → `E_VIDEO_INVALID` / `E_VIDEO_CORRUPTED`; (4) tạm: `-err_detect ignore_err` cho file lỗi nhẹ.
- **Input:** video path.
- **Output:** MediaMetadata JSON.
- **Acceptance:** Đúng metadata cho MP4/MKV/MOV fixtures; file giả lỗi trả error code đúng; rotation xử lý đúng.
- **Test Cases:** pytest fixture các format; golden metadata file; test file hỏng.
- **Potential Problems:** Rotation dính trong container metadata → normalize; fps không nguyên → lấy rational.
- **DoD:** Probe chính xác, có fixtures + golden test.

### TASK-010 | Job system (state/queue/progress/persist)
- **Goal:** JobService đầy đủ: submit, queue FIFO, state machine, progress, cancel, retry, persist, resume sau crash.
- **Why:** Phase 4 — pipeline và trạng thái xuyên suốt mọi stage.
- **Dependencies:** TASK-008
- **Files/Modules:** `src-tauri/src/services/job_service.rs`, `db/repo/job.rs`, commands `job.rs`, worker `core/job.py`
- **Implementation:** (1) bảng jobs (MASTER_PLAN §17.1) + repo; (2) JobService: `submit` → validate dep → `queued` → run; (3) state machine + transition guard; (4) progress 0-1 + stage; (5) cancel flag (worker poll) + FFmpeg kill; (6) retry auto (transient backoff 1s/5s/30s max 3) + manual; (7) resume: job status lưu DB, restart app → tiếp tục từ completed stage; (8) events `job:status`/`job:log`.
- **Input:** TASK-008.
- **Output:** Job lifecycle đầy đủ qua IPC + events.
- **Acceptance:** Job chạy → fail → retry → success; cancel giữa chạy dừng worker subprocess; restart app → job resume; progress đúng.
- **Test Cases:** Unit: state machine transitions invalid; integration: chạy job giả (mock worker) qua các state; kill giữa chạy.
- **Potential Problems:** Race khi cancel đúng lúc worker trả kết quả → handle cancelled-race; persist mỗi state change (fsync).
- **DoD:** State machine hoàn chỉnh, persist + resume, cancel an toàn, events đúng.

### TASK-011 | CacheService (keys/get/set/invalidate)
- **Goal:** Cache content-addressed cho audio/STT/translation/render; LRU theo dung lượng.
- **Why:** Phase 4 — quyết định FROZEN §3.7 (đổi style không chạy lại AI).
- **Dependencies:** TASK-008
- **Files/Modules:** `src-tauri/src/services/cache_service.rs`, `worker/src/services/cache.py`
- **Implementation:** (1) key builder theo bảng §3.7 (sha256 của inputs + params); (2) Rust: get/set/invalidate, quota LRU (10GB default, cấu hình); (3) Python: cache file access cho worker stages; (4) invalidate cascade (sửa translation → invalidate downstream subtitle/render, không đụng STT); (5) version counter khi đổi param.
- **Input:** ARCHITECTURE_DECISION §3.7.
- **Output:** Cache hit/miss đúng cho từng stage.
- **Acceptance:** Đổi subtitle style → render cache miss nhưng STT/translation hit; sửa source text → STT không chạy lại; vượt quota → LRU evict; crash không làm hỏng cache.
- **Test Cases:** Unit: key stability (cùng input → cùng key); integration: flow đổi style; test evict.
- **Potential Problems:** Hash của file lớn tốn CPU → hash nhanh (xxhash) + lưu kèm; cache corrupt → bỏ qua mục lỗi.
- **DoD:** Cache đúng ngữ nghĩa, evict hoạt động, có test cascade.

### TASK-012 | Audio extract (FFmpeg safe args)
- **Goal:** Extract WAV 16k mono từ video bằng argument array; progress; xử lý không có audio track.
- **Why:** Phase 3 — đầu vào của STT.
- **Dependencies:** TASK-009
- **Files/Modules:** `worker/src/services/audio_service.py`, `worker/src/core/ffmpeg.py`
- **Implementation:** (1) `ffmpeg.py`: safe arg builder (không shell string; validate path chặn `; | & \n`); (2) `-vn -ac 1 -ar 16000 -c:a pcm_s16le`; (3) progress parse từ `-progress pipe:1`; (4) cache key `audio:{sha256(video)}:{spec}`; (5) error → `E_FFMPEG_FAILED`, không có audio → error rõ.
- **Input:** video path + spec.
- **Output:** WAV 16k mono + progress + cache.
- **Acceptance:** Đúng spec; không thực thi chuỗi shell (unit test path chứa ký tự nguy hiểm); file không audio trả lỗi rõ; cache hit lần 2.
- **Test Cases:** Unit: arg builder escape; integration: extract từ MP4/MKV; test path injection.
- **Potential Problems:** Audio track chọn nhầm (multi-track) → option `-map` theo probe; stream rỗng → error.
- **DoD:** Extract chuẩn, an toàn, có cache, có test injection.

### TASK-013 | STT: faster-whisper integrate + progress
- **Goal:** Transcribe bằng faster-whisper (int8), segment + timestamp + confidence; batch theo VAD; progress realtime.
- **Why:** Phase 5 — lõi MVP (Transcribe).
- **Dependencies:** TASK-012
- **Files/Modules:** `worker/src/services/stt_service.py`, `api/routes.py` (`/v1/stt/transcribe`)
- **Implementation:** (1) lazy load `WhisperModel` (model: large-v3/turbo theo preset, compute_type `int8_float16` cho GPU, `int8` cho CPU); (2) Silero VAD segment trước khi transcribe; (3) batch transcribe per segment; (4) build transcript.json theo schema §24.1; (5) progress: segments processed/total → event; (6) language detect + override; (7) cancel flag giữa segment.
- **Input:** WAV 16k mono + options.
- **Output:** Transcript JSON + progress.
- **Acceptance:** 10 phút tiếng Trung → transcript segment đúng thời gian (±200ms), có confidence; cancel giữa segment dừng sạch; VRAM guard (nếu <2.5GB → turbo/small).
- **Test Cases:** Integration trên fixture 60s; benchmark 1/10/30 phút; test cancel; test OOM fallback (mock).
- **Potential Problems:** VAD cắt ngang từ → padding overlap; model download lần đầu (cần TASK-016A-D); memory leak giữa các lần → unload model.
- **DoD:** Transcript chuẩn schema, progress, cancel, VRAM guard.

### TASK-014 | STT: GPU detect + int8 + VRAM guard
- **Goal:** HardwareProbe chọn device/compute đúng; tự hạ model nếu thiếu VRAM.
- **Why:** Phase 5 — FROZEN GPU strategy; tránh OOM.
- **Dependencies:** TASK-013
- **Files/Modules:** `worker/src/services/hardware.py`, `src-tauri/src/services/hardware_probe.rs`
- **Implementation:** (1) Rust HardwareProbe: NVIDIA (nvidia-smi/NVML VRAM), AMD/Intel (WMI/dxdiag), ffmpeg encoder list, RAM; (2) Python detect torch.cuda; (3) strategy matrix (MASTER_PLAN §14.2): NVIDIA→faster-whisper CUDA; Intel/AMD→whisper.cpp Vulkan; CPU→int8; (4) VRAM guard: ước tính cần cho model, nếu thiếu → hạ model/batch hoặc CPU; (5) user override (Auto/CUDA/CPU) trong settings.
- **Input:** TASK-013, hardware.
- **Output:** HardwareProfile + strategy chọn đúng.
- **Acceptance:** Máy NVIDIA chọn CUDA; máy không GPU chọn CPU; VRAM 4GB tự hạ xuống turbo; override hoạt động; WMI thiếu → fallback nguồn khác.
- **Test Cases:** Unit strategy matrix; integration trên máy thật (3 cấu hình); mock VRAM để test guard.
- **Potential Problems:** WMI chậm/thiếu → timeout + fallback; driver cũ → cảnh báo E_CUDA_ERROR.
- **DoD:** Detect chuẩn, guard hoạt động, override được, có test 3 cấu hình.

### TASK-015 | STT: whisper.cpp fallback (CPU/AMD/Intel)
- **Goal:** Fallback whisper.cpp (Vulkan/CPU) với **3 mitigation bắt buộc**.
- **Why:** Phase 5 — FROZEN chiến lược kép (ARCHITECTURE_DECISION 2.4, 3.2).
- **Dependencies:** TASK-014
- **Files/Modules:** `worker/src/services/stt_service.py`, `worker/src/core/whisper_cpp.py`, sidecar binary `whisper-cli`
- **Implementation:** (1) bundle/ path đến `whisper-cli.exe` (build riêng, Vulkan support); (2) gọi bằng arg array: `-m model.bin -l zh -t <n> --beam-size 5` ; (3) **mitigation 1:** `beam_size ≤ 5-6`; **mitigation 2:** `--no-flash-attn` khi device Vulkan AMD/Intel; (4) **mitigation 3:** init single-threaded (semaphore); nếu build static lib → `ggml_backend_vk_reg()` thủ công sau init instance (workaround #3750); (5) convert output → transcript schema.
- **Input:** TASK-014.
- **Output:** Transcript JSON qua whisper.cpp khi cần.
- **Acceptance:** CPU-only transcribe 10 phút không crash; AMD/Intel Vulkan không segfault (beam ≤ 6); **Vulkan init fail → tự fallback CPU + log + user-visible warning (Vulkan = compatibility enhancement, KHÔNG blocker — FIX);** init 2 model cùng lúc không race; fallback tự động khi faster-whisper không có CUDA.
- **Test Cases:** Integration CPU; AMD test manual (fixtures riêng); test race (2 jobs đồng thời bị chặn); benchmark tốc độ.
- **Potential Problems:** VAD của whisper.cpp kém hơn → dùng Silero VAD ngoài + chunk; GPU device detect sai → override.
- **DoD:** Fallback hoạt động ổn định, 3 mitigations có test, có binary build script.

### TASK-016A | ModelRegistry (manifest + metadata)
- **Goal:** Manifest khai báo model với metadata chuẩn; single source cho mọi model tải/verify.
- **Why:** Phase 5 — models không bundle (MASTER_PLAN §32.4); cần registry để downloader/verifier/cache dùng chung.
- **Dependencies:** TASK-013
- **Files/Modules:** `worker/src/services/model_registry.py`, `schemas/model.schema.json`, `models/manifest.json`
- **Implementation:** (1) schema metadata bắt buộc: `id, name, version, source (HF repo/file), download URL, expected size, checksum (SHA-256), license, required VRAM, supported backend`; (2) manifest JSON là nguồn chân lý; (3) API `list()/get(id)/resolve(backend, vram)` trả model khả dụng cho device; (4) version bất biến (immutable id@version).
- **Input:** model manifest.
- **Output:** Registry hoạt động, resolve đúng theo backend/VRAM.
- **Acceptance:** Model có đủ metadata bắt buộc; resolve đúng cho CUDA/Vulkan/CPU; thiếu metadata → model không nằm trong list available.
- **Test Cases:** Unit: manifest parse, resolve matrix (backend + VRAM).
- **Potential Problems:** Model mới thêm → manifest update + version bump; không hard-code model name rải rác.
- **DoD:** Registry FROZEN, có test, là nguồn duy nhất cho downloader/verifier/cache.

### TASK-016B | ModelDownloader (progress + resume)
- **Goal:** Tải model từ HF/URL có progress + resume; hỗ trợ import thủ công offline.
- **Why:** Phase 5 — model phải tải lần đầu; mạng không ổn định.
- **Dependencies:** TASK-016A
- **Files/Modules:** `worker/scripts/download_models.py`, `worker/src/services/model_downloader.py`
- **Implementation:** (1) tải qua `huggingface_hub` snapshot_download (resume) hoặc HTTP range cho file thô; (2) progress event (bytes/total); (3) lưu vào `user-data/models/<id>@<version>/`; (4) import thủ công (chọn file/thư mục) khi offline — đăng ký vào cache với checksum tự tính; (5) không tải lại nếu cache đã có version hợp lệ.
- **Input:** registry entry + target dir.
- **Output:** Model trong cache + progress event.
- **Acceptance:** Download có resume (ngắt mạng → tiếp tục); import thủ công hoạt động; không re-download nếu đã có.
- **Test Cases:** Integration: mock HF endpoint; test resume (hủy giữa chừng); test import thủ công.
- **Potential Problems:** HF rate limit → retry backoff; disk full → E_DISK_FULL.
- **DoD:** Download/resume/import đủ, progress event, không tải thừa.

### TASK-016C | ModelVerifier (checksum + license + kích thước)
- **Goal:** Verify model sau tải/import: checksum, kích thước, license trong manifest; **checksum không khớp → KHÔNG đánh dấu available**.
- **Why:** Phase 5 — FIX #6; chống model corrupt/giả/nhầm.
- **Dependencies:** TASK-016A
- **Files/Modules:** `worker/src/services/model_verifier.py`
- **Implementation:** (1) tính SHA-256 từng file sau tải; so sánh manifest; (2) kích thước + đủ file (fixture manifest liệt kê file bắt buộc); (3) license field bắt buộc — model không có license rõ → đánh dấu `unverified` (không chặn dev, chặn release theo checklist licensing); (4) trạng thái: `downloading → verifying → ready / corrupt`; corrupt → cho re-download/remove.
- **Input:** registry entry + đường dẫn đã tải.
- **Output:** Trạng thái verify đúng (ready/corrupt/unverified).
- **Acceptance:** File sửa 1 byte → corrupt; thiếu file → corrupt; license trống → unverified; chỉ `ready` mới xuất hiện trong list available.
- **Test Cases:** Unit: checksum đúng/sai, thiếu file, license trống.
- **Potential Problems:** Checksum file lớn chậm → hash theo luồng (chunk), hiển thị progress.
- **DoD:** Verify đúng 3 trạng thái, có test, checksum fail → không available.

### TASK-016D | ModelCache (lưu trữ + import + không tải lại)
- **Goal:** Quản lý `user-data/models/`; key theo `(id, version)`; không re-download khi đã có hợp lệ; import thủ công offline.
- **Why:** Phase 5 — FIX #6; cache chuẩn cho model.
- **Dependencies:** TASK-016A, TASK-016C
- **Files/Modules:** `worker/src/services/model_cache.py`
- **Implementation:** (1) cấu trúc thư mục `<id>@<version>/` + file metadata; (2) `has(id,version) → bool` (chỉ true khi verified ready); (3) lưu kèm checksum tính được để verify lại khi cần; (4) import thủ công → chạy verifier rồi đăng ký; (5) xóa model (user) → dọn cache + registry.
- **Input:** registry entry, verifier status.
- **Output:** Cache đúng ngữ nghĩa; model ready không bị tải lại.
- **Acceptance:** Model verified → lần 2 không tải; corrupt model không dùng; xóa model dọn sạch.
- **Test Cases:** Unit: cache hit/miss; integration: import → verify → ready.
- **Potential Problems:** Disk đầy → cảnh báo + yêu cầu user xóa; 2 version cùng id → giữ cả hai, cho chọn.
- **DoD:** Cache hoạt động, không tải lại model ready, import thủ công OK.

### TASK-017 | TranslationProvider interface + MockProvider
- **Goal:** `TranslationProvider` Protocol + MockProvider (test không cần mạng) + registry.
- **Why:** Phase 6 — FROZEN provider abstraction; mọi provider sau đều implement interface.
- **Dependencies:** TASK-007
- **Files/Modules:** `worker/src/services/providers/base.py`, `translation/mock_provider.py`
- **Implementation:** (1) Protocol `name`, `translate_block(block, context) -> TranslatedBlock`, `estimate_cost()`, `health()`; (2) MockProvider: trả translation giả theo map có sẵn, có thể inject lỗi để test; (3) registry theo tên; (4) schema TranslatedBlock khớp §24.2.
- **Input:** ARCHITECTURE_DECISION §3.3.
- **Output:** Interface chuẩn + mock hoạt động.
- **Acceptance:** Mock dịch đúng số block; inject fail → trả lỗi chuẩn; registry resolve đúng provider.
- **Test Cases:** Unit: interface contract (mock), registry, error injection.
- **Potential Problems:** Interface quá chung → chốt schema trước, thêm field sau qua version.
- **DoD:** Interface FROZEN, mock có test, registry OK.

### TASK-018 | OpenAI provider (JSON mode) — **[Post-MVP, implement sau TASK-020]**
- **Goal:** Provider OpenAI `gpt-4o-mini` với JSON mode; structured output. **KHÔNG nằm trong path MVP** — implement sau TASK-020 khi cần mở rộng provider (FIX #7).
- **Why:** Phase 6 (V1+) — provider mở rộng; MVP chỉ cần Gemini + Local LLM.
- **Dependencies:** TASK-017
- **Files/Modules:** `worker/src/services/providers/translation/openai_provider.py`
- **Implementation:** (1) gọi `chat.completions` với `response_format: json_object`; (2) prompt theo template MASTER_PLAN §12.2; (3) parse + validate JSON về TranslatedBlock; (4) retry với backoff cho rate limit; (5) base_url configurable (OpenAI-compatible).
- **Input:** API key (từ Rust qua env), block.
- **Output:** TranslatedBlock JSON.
- **Acceptance:** Dịch 10 cues → JSON hợp lệ, không miss line; rate limit → backoff; key sai → E_API_AUTH.
- **Test Cases:** Unit: parse/validate; integration: 1 real call (marker `ai`), mock server cho rate limit.
- **Potential Problems:** JSON không hợp lệ → repair (sanitize) + retry; token budget → chunk nhỏ.
- **DoD:** Provider hoạt động, có mock test + 1 real smoke test.

### TASK-019 | Gemini provider
- **Goal:** Provider Gemini (2.5 Flash-Lite default; Flash cho High) với responseSchema.
- **Why:** Phase 6 — default model sau S1; rẻ + 1M context.
- **Dependencies:** TASK-017
- **Files/Modules:** `worker/src/services/providers/translation/gemini_provider.py`
- **Implementation:** (1) dùng `google-genai` SDK; (2) `responseSchema` structured output; (3) config model từ settings (mặc định `gemini-2.5-flash-lite`); (4) context caching nếu provider hỗ trợ (ghi chú: gemini caching tách endpoint — xét khi cần); (5) retry backoff + E_API_* mapping.
- **Input:** API key, block.
- **Output:** TranslatedBlock JSON.
- **Acceptance:** Dịch đúng target; schema validate; model default = 2.5 Flash-Lite (S1); lỗi mapping đúng.
- **Test Cases:** Unit parse; integration 1 real call (marker `ai`); mock cho lỗi.
- **Potential Problems:** Gemini output đôi khi không khớp schema 100% → validate + repair; model name đổi → không hard-code, đọc từ settings.
- **DoD:** Provider hoạt động, default đúng S1, có test.

### TASK-020 | Local LLM provider (llama.cpp OpenAI-compat)
- **Goal:** Provider local qua llama.cpp server (OpenAI-compatible `/v1/chat/completions`) + Qwen GGUF.
- **Why:** Phase 6 — offline fallback; không lock-in cloud.
- **Dependencies:** TASK-017
- **Files/Modules:** `worker/src/services/providers/translation/local_llm_provider.py`, script khởi động llama-server
- **Implementation:** (1) quản lý vòng đời `llama-server` (port random, model GGUF Qwen 7-14B, quant Q4_K_M); (2) gọi OpenAI-compat endpoint với token từ settings; (3) JSON output mode nếu model hỗ trợ; (4) health check model loaded; (5) tắt server khi không dùng (giải phóng RAM).
- **Input:** path GGUF.
- **Output:** TranslatedBlock JSON.
- **Acceptance:** Dịch block local khi cloud off; server dừng đúng khi xong; VRAM guard (model quá to cho VRAM → CPU quant nhỏ hơn).
- **Test Cases:** Integration (nếu có model test nhỏ, marker `ai`); mock server OpenAI-compat; test lifecycle.
- **Potential Problems:** GGUF download lớn → dùng TASK-016A-D; chất lượng kém hơn cloud → chỉ fallback, cảnh báo; server port conflict.
- **DoD:** Fallback local hoạt động, lifecycle đúng, có test mock.

### TASK-021 | Context Engine + chunking + overlap
- **Goal:** Build context (prev/next block, speaker khi có, glossary, rules); chunk block 5-10 cues + overlap 2. **Scene context = Post-MVP** (không cần scene detection ở MVP — FIX #4).
- **Why:** Phase 6 — FROZEN §3.3; chất lượng dịch phụ thuộc context.
- **Dependencies:** TASK-019, TASK-020
- **Files/Modules:** `worker/src/services/context_service.py`
- **Implementation:** (1) chunking theo semantic boundary (hội thoại) + giới hạn cues; (2) overlap: gửi 2 block trước làm read-only context; (3) ContextEngine (MVP): glossary matches, character dict, rules, speaker map (nếu có diarization metadata), prev/next text; (4) token budget guard (<70% context window); (5) prompt builder chuẩn theo template. KHÔNG thêm scene/emotion/mối quan hệ nhân vật vào context ở MVP (V1+ — MASTER_PLAN §3.3/§12.1).
- **Input:** segments, glossary, chars, rules.
- **Output:** Chunks + context pack.
- **Acceptance:** Chunk 100 cues → đúng 10-20 blocks; overlap chứa 2 block trước; prompt <70% context; glossary match đúng.
- **Test Cases:** Unit: chunk boundaries, overlap nội dung, token budget.
- **Potential Problems:** Prompt quá dài → giảm chunk; không có speaker → fallback `speaker_00`.
- **DoD:** Chunking + context đúng, có test.

### TASK-022 | Translation validation + retry + QC
- **Goal:** Validate output (schema, số dòng = input, target lang), retry backoff, QC cơ bản + hallucination.
- **Why:** Phase 6 — FROZEN §3.3; chống miss line/hallucination.
- **Dependencies:** TASK-021
- **Files/Modules:** `worker/src/services/quality_service.py`
- **Implementation:** (1) validate: số translation = số segment; idx khớp; không empty; (2) target lang detect (fasttext/thư viện nhẹ) → sai thì đánh dấu; (3) CPS check (quá dài → flag rewrite); (4) retry: transient (timeout/rate) backoff 1s/5s/30s max 3; permanent → dừng, giữ bản tốt nhất; (5) QC basic: báo cáo per block.
- **Input:** TranslatedBlock.
- **Output:** translation.json QC-pass hoặc report lỗi.
- **Acceptance:** LLM trả thiếu line → retry/repair lấy đủ; target sai lang → flag; không miss line cuối cùng.
- **Test Cases:** Unit: validator với input thiếu/thừa; integration: mock LLM trả lỗi → retry.
- **Potential Problems:** LLM thêm nội dung → so sánh idx duy nhất; retry tốn token → giới hạn + backoff.
- **DoD:** Validation + retry + QC hoạt động, có test.

### TASK-023 | Glossary + character dict + translation memory
- **Goal:** Glossary terms, character dictionary, TM (skip trùng tiết kiệm chi phí).
- **Why:** Phase 6 — MASTER_PLAN §8.4.2/8.4.3; cost control + chất lượng.
- **Dependencies:** TASK-022
- **Files/Modules:** `db/repo/glossary.rs`, `db/repo/characters.rs`, `worker/src/services/translation_service.py` (TM)
- **Implementation:** (1) bảng glossary + characters (MASTER_PLAN §17.1); (2) CRUD qua IPC + UI cơ bản; (3) TM: `(source_hash, target, glossary_ver, model)` → lookup trước khi gọi LLM; (4) invalidate khi đổi glossary.
- **Input:** terms, chars.
- **Output:** Glossary áp dụng vào prompt; TM skip trùng.
- **Acceptance:** Thêm glossary → bản dịch sau dùng đúng thuật ngữ; segment trùng lặp → không gọi LLM lần 2; đổi glossary → TM cũ invalid.
- **Test Cases:** Unit: TM lookup/hit/miss; integration: glossary effect qua mock LLM (assert prompt chứa term).
- **Potential Problems:** TM quá rộng → scope per project + lang; hash collision → hash dài.
- **DoD:** Glossary + TM hoạt động, có test.

### TASK-024 | SubtitleEngine (cues, line break, CPS, ASS/SRT)
- **Goal:** Sinh cues + ASS/SRT/VTT từ translation; line break, CPS, style. **Line break theo policy configurable (KHÔNG hard-code 42) — FIX subtitle policy.**
- **Why:** Phase 7 — đầu vào của render và editor.
- **Dependencies:** TASK-023
- **Files/Modules:** `worker/src/services/subtitle_service.py`
- **Implementation:** (1) group/merge cues liền nhau cùng speaker (nếu cần); (2) **line break policy:** `max_chars_per_line` là hàm theo (ngôn ngữ, font metrics, display width, safe-area, độ dài từ) — configurable trong style, không universal hard-code; ưu tiên ngắt tại dấu câu/không ngắt cụm từ; (3) CPS check ≤ ngưỡng config (mặc định 15-20) → tăng duration hoặc cảnh báo; (4) padding 50-80ms; (5) sinh ASS (style đầy đủ: font/size/stroke/shadow/position/bg) + SRT/VTT; (6) validate bằng ffmpeg parse.
- **Input:** translation.json + style config.
- **Output:** subtitle.ass/srt + cues.
- **Acceptance:** ASS mở bằng ffmpeg/VLC không lỗi; line break theo policy (không vượt `max_chars_per_line` đã config, không có hard-code 42); CPS không vượt (hoặc có cảnh báo); timing không chồng nhau.
- **Test Cases:** Unit: line break policy (đổi config → đổi kết quả), CPS, merge; integration: sinh file → ffprobe/ffmpeg parse OK.
- **Potential Problems:** Tiếng Việt dấu → đếm ký tự Unicode (grapheme, không phải byte); font thiếu → danh sách font fallback.
- **DoD:** Subtitle đúng chuẩn, policy configurable, có test parse.

### TASK-025 | Subtitle editor UI
- **Goal:** Bảng cues chỉnh text/timing/speaker; sync source ↔ translation.
- **Why:** Phase 8 — người dùng duyệt/sửa trước render.
- **Dependencies:** TASK-024
- **Files/Modules:** `src/pages/Project/SubtitleEditorView.tsx`, `subtitle.update_cue` command
- **Implementation:** (1) bảng: time, speaker, source, translation, status; (2) inline edit text/time; (3) debounce save → DB; (4) filter/search; (5) lưu thủ công (Ctrl+S) + auto-save.
- **Input:** cues từ DB.
- **Output:** Cues đã sửa trong DB.
- **Acceptance:** Sửa 1 cue → DB cập nhật (refresh không mất); undo trong session; hiển thị đúng khi có >1000 cues.
- **Test Cases:** Vitest component (edit, save, undo); integration: save → reload → dữ liệu còn.
- **Potential Problems:** Render 5000 cues chậm → virtualization; autosave conflict → optimistic update + version.
- **DoD:** Editor dùng được, autosave, virtualized.

### TASK-026 | Preview (video + overlay)
- **Goal:** Preview video với subtitle overlay (HTML, MVP); scrub. **Overlay phải khớp vị trí/font/size/stroke/shadow với ASS defaults (bottom-center, safe-area) — preview ≈ final render (FIX).**
- **Why:** Phase 8 — kiểm tra subtitle trước render; tránh user thấy preview khác hẳn output cuối.
- **Dependencies:** TASK-025
- **Files/Modules:** `src/pages/Project/PreviewView.tsx`, `components/VideoPreview.tsx`
- **Implementation:** (1) `<video>` source = file path qua Tauri protocol scoped; (2) overlay caption theo cue hiện tại (thời gian từ `timeupdate`); (3) **style đồng bộ với ASS defaults** (font family/size, position bottom-center, safe-area, stroke, shadow, background) — đọc từ cùng config SubtitleService; deviation chấp nhận được ghi rõ (VD ±4px, timing ±50ms); note: MVP không phải libass live (tech debt §42); (4) scrub + play/pause.
- **Input:** video + cues + style config.
- **Output:** Preview hoạt động.
- **Acceptance:** Overlay đúng cue theo thời gian; position/font/stroke/shadow khớp ASS defaults trong ngưỡng ghi rõ; video path không expose ra ngoài.
- **Test Cases:** Vitest component (timeupdate → cue hiện; assert class/style map đúng config); visual test so preview frame vs render frame (manual/threshold).
- **Potential Problems:** Path unicode/file protocol → dùng Tauri asset protocol scoped; overlay lệch timing ±50ms → chấp nhận MVP.
- **DoD:** Preview đúng cue, style khớp ASS defaults, có test.

### TASK-027 | RenderService (libass burn-in + encoder auto + progress)
- **Goal:** Burn-in ASS bằng libass; chọn encoder tự động; progress/ETA; cancel; fallback encoder. **Kèm render validation bắt buộc (FIX) — không xuất file hỏng im lặng.**
- **Why:** Phase 9 — đầu ra video cuối.
- **Dependencies:** TASK-024
- **Files/Modules:** `worker/src/services/render_service.py`, `core/ffmpeg.py`
- **Implementation:** (1) `ffmpeg -i in -vf "ass=sub.ass,subtitles" -c:v <encoder> -progress pipe:1` (arg array); (2) encoder detect: NVENC→QSV→AMF→libx264/x265 (từ TASK-014 strategy); (3) preserve resolution/FPS/SAR/color metadata; (4) progress: out_time/duration → % + ETA; (5) cancel: kill FFmpeg gracefully + dọn temp; (6) fallback: NVENC fail → libx264 (log + thông báo); (7) output ra temp file → rename atomic; (8) **render validation:** ffprobe output → verify resolution == input/config, FPS, audio track còn nguyên (kênh/tần số), codec/container hợp lệ, burn-in có subtitle (frame sample có text region), duration ≈ source (±1s); fail → báo lỗi rõ (E_RENDER_VALIDATION), KHÔNG cung cấp file như bình thường.
- **Input:** video + sub + preset.
- **Output:** output.mp4 + progress + validation report.
- **Acceptance:** Render 10 phút giữ resolution/FPS; audio nguyên; duration đúng; cancel dọn temp không còn file; NVENC fail → tự fallback libx264; validation bắt lỗi corrupt (ép codec sai → fail).
- **Test Cases:** Integration: render short clip các codec; golden hash; test cancel; test fallback (ép encoder fail); test validation (ép sai resolution).
- **Potential Problems:** Hardware encoder artifact/khác màu → preset phù hợp; aspect anamorphic → SAR đúng; disk full → E_DISK_FULL.
- **DoD:** Render chuẩn, progress, cancel, fallback, atomic rename, validation bắt lỗi.

### TASK-028 | Watermark (text + image)
- **Goal:** Watermark text + image qua filter_complex; 9 vị trí + custom.
- **Why:** Phase 9 — yêu cầu functional §3.7.
- **Dependencies:** TASK-027
- **Files/Modules:** `render_service.py` (extend), `components/WatermarkConfig.tsx`
- **Implementation:** (1) text: `drawtext` (font, size, color, opacity, position, rotation, margin) — escape đúng; (2) image: `overlay` (scale, opacity, position); (3) 9 vị trí + custom x/y; (4) render cache key gồm watermark params.
- **Input:** config watermark.
- **Output:** Watermark đúng trong output.
- **Acceptance:** Text/image đúng vị trí/opacity; ký tự đặc biệt không phá lệnh; đổi watermark → render cache miss.
- **Test Cases:** Unit: escape drawtext text; integration: render có watermark → kiểm tra pixel region / golden.
- **Potential Problems:** drawtext escape ký tự `:` `'` → escape đúng; font path unicode.
- **DoD:** Watermark hoạt động, có test escape.

### TASK-029 | Export (video + subtitle files) + QC
- **Goal:** Export video + SRT/VTT/ASS ra thư mục user; QC bằng ffprobe (duration/resolution/streams/audio/burn-in).
- **Why:** Phase 9 — sản phẩm giao cho user; QC xác nhận output hợp lệ trước khi bàn giao.
- **Dependencies:** TASK-027
- **Files/Modules:** `render_service.py` (export), commands `export.video/subtitles`, `ExportView.tsx`
- **Implementation:** (1) export: copy output video (rename) + ghi file subtitle ra thư mục đích; (2) QC: ffprobe output → verify duration ≈ source ±1s, resolution, 1 video stream, audio stream đúng (còn nguyên), subtitle streams nếu mux, container hợp lệ; (3) báo cáo QC (pass/warn); (4) lỗi → E_PERMISSION_DENIED/E_DISK_FULL.
- **Input:** output video + sub files + target dir.
- **Output:** File tại thư mục user + QC report.
- **Acceptance:** Export đúng tên/định dạng; QC pass đúng tiêu chí (resolution/FPS/duration/audio/burn-in); thư mục không ghi được → lỗi rõ.
- **Test Cases:** Integration: export → ffprobe verify; test path không hợp lệ.
- **Potential Problems:** Tên file trùng → auto-suffix; path quá dài (Windows MAX_PATH) → dùng `\\?\` prefix khi cần.
- **DoD:** Export + QC hoạt động, có test.

### TASK-030 | Settings UI + secrets + error UI
- **Goal:** Settings (AI/GPU/API masked/cache/privacy), SecretStore wiring, ErrorBoundary + toast + job fail banner.
- **Why:** Phase 10 — cấu hình + bảo mật + trải nghiệm lỗi.
- **Dependencies:** TASK-010, TASK-011
- **Files/Modules:** `src/pages/Settings/*`, `src-tauri/src/security/secret_store.rs`, `components/ErrorBoundary.tsx`, `JobProgress.tsx`
- **Implementation:** (1) SecretStore: Windows Credential Manager (keyring crate), `set_api_key` command, UI chỉ masked (`sk-****last4`); (2) Settings views: AI (model/device/preset), GPU (auto/override), API (providers + base_url + key), cache (quota), privacy (mode, telemetry); (3) Error UI: ErrorBoundary app-level, toast, job failed banner (code + retry + view logs); (4) persist settings DB + validate.
- **Input:** TASK-010, TASK-011.
- **Output:** Settings dùng được; key an toàn; lỗi friendly.
- **Acceptance:** Lưu key → Credential Manager (không phải DB); UI hiện masked; đổi settings → áp dụng ngay (job mới); lỗi job hiện banner + retry hoạt động; không crash toàn app khi component lỗi.
- **Test Cases:** Unit: secret store roundtrip (mock); vitest: masked display, ErrorBoundary; integration: settings → worker nhận config mới.
- **Potential Problems:** Keyring trên máy không có DPAPI/credential service → **fail-safe (FIX #8):** chặn lưu key + thông báo rõ; KHÔNG fallback encrypted-file/custom crypto; lộ key trong log → strict no-log.
- **DoD:** Settings + secrets + error UI hoàn chỉnh, có test.

---

## 5. TASK SAU MVP (không thuộc 30 task đầu — để dành)

```text
T031: Diarization production (pyannote, UI gán speaker)      [V1]
T032: Word-level timestamps (WhisperX align)                  [V1]
T033: Scene detection cho context                             [V1]
T034: LLM-as-judge quality (preset High/Maximum)              [V1]
T035: Burned-in subtitle removal (RapidOCR + STTN/LaMa)       [V1]
T036: Audio separation (Demucs)                               [V1]
T037: TTS dubbing + timing alignment                          [V1]
T038: Auto-update production                                  [Phase 14]
T039: Licensing/commercial (offline grace, credits)           [V1]
T040: Segmentation render resume (keyframe-based)             [V1]
```

---

## 6. LICENSING CHECKLIST (bắt buộc trước Phase 13 release)

- [ ] Verify từng LICENSE file + model card trong `vendor/` + `worker/` deps (cargo-deny + pip-licenses).
- [ ] Xác nhận KHÔNG có: ProPainter, XTTS v2, F5-TTS, Viterbox, Kokoro-Vietnamese community, Piper fork GPL (nếu embed trong closed-source).
- [ ] FFmpeg build: bảng kê LGPL compliance (libass/x264 đã ghi rõ).
- [ ] Whisper models (MIT) attribution ghi trong About/LICENSING.md.
- [ ] pyannote community-1: hướng dẫn user accept agreement + ghi attribution (CC-BY-4.0).
- [ ] Qwen GGUF: model card Apache-2.0 ghi chú.
- [ ] Lưu tất cả kết quả vào `LICENSING.md` (đã có trong §22).

---

## 7. DO DỰA TRÊN TOÀN MVP (3 tầng — kiểm tra trước khi đóng release)

**Tầng 1 — Technical:**
```text
[ ] Build + install Win10/11 mới (không cần Python/Node/Rust/FFmpeg/CUDA); uninstall sạch
[ ] Test suite xanh: unit + integration + E2E + benchmark 1/10/30/60 phút
[ ] Không crash critical khi cancel/OOM/API fail; bảng lỗi core đúng code
[ ] Không regression bảo mật: key chỉ Credential Manager, không fallback encrypted-file (FIX #8)
[ ] Installer signed, SmartScreen pass; auto-update + rollback test
```

**Tầng 2 — Product:**
```text
[ ] Pipeline Import→STT→Translate→Subtitle→Render→Export chạy end-to-end 10 phút Trung→Việt
[ ] CPU-only + NVIDIA GPU đều chạy (1 installer hoặc runtime add-on)
[ ] Cache đúng (đổi style = chỉ render; sửa translation = không chạy STT)
[ ] Cancel giữa render dọn temp; resume không chạy lại AI
[ ] MVP POLISH: editor, preview, watermark, settings, privacy mode hoạt động
```

**Tầng 3 — Quality:**
```text
[ ] GOLDEN_VIDEO_TEST.md: STT checkpoint bắt buộc PASS (timing ±200ms, không miss segment)
[ ] QUALITY_BENCHMARK.md: translation đạt ngưỡng score trên Golden Translation Dataset
[ ] Subtitle readability: line break theo policy, CPS ≤ ngưỡng, timing padding đúng
[ ] Render validation pass: resolution/FPS/audio/codec/container/duration/burn-in
[ ] Docs đủ (README, ARCHITECTURE, DEVELOPMENT, AI/VIDEO/AUDIO_PIPELINE, DATABASE, API, SECURITY, LICENSING, TESTING, RELEASE)
[ ] Không có dependency non-commercial trong release
```

---

*Hết TASKS.md — Sprint 0 (Foundation) bắt đầu từ TASK-001. Mọi task phải tuân format ở Mục 1.*

## 8. AI CODING AGENT EXECUTION POLICY (bắt buộc)

> Dành cho mọi AI coding agent làm việc trên repo này. Vi phạm → dừng task.

```text
1. ONE TASK = ONE GATE (mỗi task là một đơn vị độc lập, có gate riêng):
   - Làm xong TASK → chạy đúng test/acceptance của task đó.
   - Gate FAIL → STOP. Không chuyển sang task tiếp theo. Báo cáo blocker với log/error.
   - Gate PASS → mới được bắt đầu task tiếp theo (theo dependency graph).

2. KHÔNG tự ý đổi architecture/scope/provider/model khi chưa có approval:
   - Mọi thay đổi vượt phạm vi task → dừng lại, trình bày proposal, chờ chấp thuận.
   - Không thêm dependency mới vào MVP nếu chưa có ADR (ARCHITECTURE_DECISION §1).

3. KHÔNG mở rộng MVP scope (dubbing/separation/OCR removal/voice cloning/timeline/
   billing/cloud backend không thuộc MVP) — tránh trôi scope.

4. Chất lượng bắt buộc trước khi báo "xong":
   - Test chạy được + pass (cả test mới cho phần vừa viết).
   - Không regression bảo mật (secret/log/credential).
   - Không hard-code: model name, API key, license kết luận, tham số subtitle (42...).

5. Nếu task phụ thuộc dữ liệu chưa có (model, transcript, API key, dataset):
   - Dùng fixture/mock theo quy ước (mock transcript, MockProvider, mock HF) — không bịa số liệu.

6. Kết thúc task → cập nhật checklist/trạng thái tương ứng trong TASKS.md nếu task đó
   yêu cầu (ghi log thay đổi), rồi báo cáo: TASK, gate result, test chạy, files changed.
```

---

*Hết TASKS.md — Sprint 0 (Foundation) bắt đầu từ TASK-001. Mọi task phải tuân format ở Mục 1.*
