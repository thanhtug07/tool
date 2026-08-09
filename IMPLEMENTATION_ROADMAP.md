# IMPLEMENTATION_ROADMAP.md — Lộ trình triển khai MVP

**Version:** 1.0.0
**Ngày:** 2026-08-09
**Base:** `MASTER_PLAN.md` (sau S1-S6) + `ARCHITECTURE_DECISION.md` (FROZEN).
**Quy tắc:** Không code trước Phase 1. Mỗi phase có cổng kiểm tra (gate) — chưa qua gate thì không xuống phase sau.

---

## 1. NGUYÊN TẮC TRIỂN KHAI

```text
1. Đi theo thứ tự phase — mỗi phase = 1 milestone có DoD rõ.
2. Foundation trước (repo, CI, schema, secrets) trước bất kỳ feature nào.
3. Chạy được end-to-end sớm (skeleton) → thêm chi tiết sau.
4. Mỗi phase: Goal → Tasks → Implementation order → Test → DoD.
5. Kiểm chứng Internet trước các quyết định nhạy cảm thời gian (model/pricing/license).
6. Đánh dấu mọi thứ chưa chắc chắn bằng TODO — VERIFY, không đoán.
```

---

## 2. SƠ ĐỒ PHỤ THUỘC (Dependency Graph)

```text
Phase 1  ──────────────────────────────────────────────┐
   │                                                   │
Phase 2 (shell+worker) ──┬─────────────────────────────┤
   │                      │                           │
Phase 3 (video engine) ───┼────────────────────────────┤
   │                      │                           │
Phase 4 (job+cache) ──────┼────────────────────────────┤
   │                      │                           │
Phase 5 (STT) ◄───────────┴─ (cần Phase 2 worker + Phase 4 jobs)
   │
Phase 6 (translation) ◄── (cần Phase 5 transcript + providers)
   │
Phase 7 (subtitle engine) ◄── (cần Phase 6 translation)
   │
Phase 8 (editor+preview) ◄── (cần Phase 7)
   │
Phase 9 (render+export) ◄── (cần Phase 7; có thể song song UI Phase 8)
   │
Phase 10 (settings+GPU+errors) ◄── (song song 8-9, cần Phase 4)
   │
Phase 11 (optimization+bench)
   │
Phase 12 (testing matrix)
   │
Phase 13 (packaging+signing+licensing verify)
   │
Phase 14 (beta + auto-update)
   │
Phase 15 (release)
```

**Ghi chú:** Phase 8 (UI) và Phase 9 (render backend) có thể làm **song song** sau Phase 7 vì không chặn nhau — render cần subtitle engine (7), editor cần cues (7). Phase 10 song song với 8-9.

**FIX #10 — Translation ↔ STT dependency:** translation (Phase 6) **dev được song song** với STT (Phase 5) nhờ **mock transcript fixtures** (`fixtures/transcripts/*.json`) — Phase 6 không chặn bởi STT thật. **Runtime bắt buộc:** `STT → Transcript → Context Engine → Translation` (không tạo translation trước transcript thật).

---

## 3. CHI TIẾT TỪNG PHASE

### Phase 1 — Project Foundation
- **Goal:** repo sạch, CI xanh, schema dùng chung, môi trường dev chuẩn.
- **Tasks:** repo + README + .gitignore + AGENTS.md; CI (lint/build/test); schema JSON versioned (transcript/translation/subtitle/job/api) + Pydantic + TS types; `secret_store.rs` (Credential Manager) sớm.
- **Dependencies:** — (khởi đầu)
- **Cổng (gate):** CI xanh trên Windows; schema validate 2 chiều; secret lưu/đọc được trong test.

### Phase 2 — Desktop Shell + Worker
- **Goal:** app mở cửa sổ; worker Python spawn/health/restart được; IPC typed ping hoạt động.
- **Tasks:** Tauri 2 + React/Vite/Tailwind shell; bridge IPC typed; Python worker skeleton (FastAPI + GET /health + jobs skeleton); WorkerManager (spawn, health poll, restart tối đa 3 lần, kill khi exit); auth token qua stdin + Bearer header; log structured.
- **Gate:** `/health` OK từ Rust; crash worker → restart; token không lộ trong argv/log.

### Phase 3 — Video Engine
- **Goal:** probe metadata chuẩn; extract audio chuẩn; mọi lệnh FFmpeg an toàn (arg array).
- **Tasks:** MediaProbeService (ffprobe JSON); AudioService (extract 16k mono, cache key); `ffmpeg.py` (safe arg builder + progress parser).
- **Gate:** probe đúng resolution/fps/duration/rotation trên fixtures MP4/MKV/MOV; extract ra WAV đúng spec.

### Phase 4 — Job System + Cache
- **Goal:** pipeline queue, state machine, persist, cancel, cache.
- **Tasks:** JobService (submit/status/cancel/retry, DB persist); state machine; CacheService (keys, get/set/invalidate, LRU); worker-side job executor; polling bridge (500ms).
- **Gate:** job chạy → persist → restart app → resume trạng thái; cancel giữa job dọn temp; cache hit/miss đúng.

### Phase 5 — STT
- **Goal:** transcript tiếng Trung/Việt local với timestamp + confidence.
- **Tasks:** STTService (faster-whisper int8, VAD Silero, batch); GPU detect + device strategy; **3 mitigation whisper.cpp Vulkan**; whisper.cpp fallback (CPU/AMD/Intel) — **Vulkan = compatibility enhancement, KHÔNG blocker**: init fail → CPU fallback + user-visible warning; model management 4 modules (ModelRegistry / ModelDownloader / ModelVerifier / ModelCache — TASK-016A-D, progress + resume + checksum verify + import thủ công); VRAM guard; diarization optional (pyannote, HF token).
- **Gate:** 10 phút tiếng Trung → transcript có segment timestamp đúng; chạy CPU-only + NVIDIA; AMD (nếu có) không crash (beam ≤ 6, flash_attn off); Vulkan init fail → fallback CPU không crash.

### Phase 6 — Translation
- **Goal:** dịch contextual, structured output, glossary + TM, cost control.
- **Tasks:** TranslationProvider interface + Mock; **MVP providers: Gemini + Local (llama.cpp OpenAI-compat)** — OpenAI/Anthropic/DeepSeek = Post-MVP (interface sẵn, thêm sau); Context Engine (prev/next block, speaker khi có, glossary, rules — scene = Post-MVP); chunking + overlap; validation + retry + QC cơ bản; glossary + character dict + translation memory; preset routing (Fast/Balanced/High/Maximum) với model mới (Gemini 2.5 Flash-Lite/Flash).
- **Dev note (FIX #10):** translation phát triển **song song** với STT nhờ mock transcript fixtures (`fixtures/transcripts/*.json`) — không chờ STT thật; runtime vẫn bắt buộc `STT → transcript → Context Engine → Translation`.
- **Gate:** 100 cues tiếng Trung (từ fixture hoặc STT) → dịch đúng target, không miss line, JSON hợp lệ; retry khi API fail; glossary áp dụng được; cost estimate hiển thị.

### Phase 7 — Subtitle Engine
- **Goal:** cues + ASS/SRT/VTT hợp lệ, line break, CPS check.
- **Tasks:** SubtitleService (group cues, merge, line break theo policy `max_chars_per_line` configurable theo ngôn ngữ/font/display width — KHÔNG hard-code 42, CPS ≤ 15-20 config, timing padding 50-80ms, style → ASS); export SRT/VTT/ASS.
- **Gate:** ASS/SRT mở bằng tool chuẩn (ffmpeg/VLC) không lỗi; style đúng vị trí/font/stroke/shadow.

### Phase 8 — Subtitle Editor + Preview
- **Goal:** UI sửa text/timing/speaker; preview video + overlay.
- **Tasks:** SubtitleEditorView (bảng cues, cập nhật DB); PreviewView (`<video>` + caption overlay HTML, MVP) — **overlay position/font/size/stroke/shadow khớp ASS defaults** (bottom-center, safe-area); Sync source ↔ translation.
- **Gate:** sửa cue → DB cập nhật → preview đúng timing; không refresh mất trạng thái; preview position khớp final render trong ngưỡng cho phép.

### Phase 9 — Render + Watermark + Export
- **Goal:** burn-in libass, encoder auto, watermark, export, QC.
- **Tasks:** RenderService (libass + filter_complex + encoder auto-detect NVENC→QSV→AMF→libx264; progress/ETA; cancel; fallback encoder); **render validation bắt buộc** — verify output resolution/FPS/audio/codec/container/duration (±1s) + burn-in, KHÔNG xuất file hỏng im lặng; Watermark (text + image); Export (video + subtitle files) + QC (ffprobe duration/resolution/streams).
- **Gate:** render 10 phút MP4 giữ resolution/FPS; watermark đúng vị trí; cancel giữa render dọn temp; QC/render-validation pass.

### Phase 10 — Settings + GPU + Error UI
- **Goal:** settings UI (AI/GPU/API masked/cache/privacy); hardware probe UI; error UI chuẩn.
- **Tasks:** SettingsViews; HardwareProbe (nvidia-smi/WMI/ffmpeg encoders); SecretStore wiring; ErrorBoundary + toast + job failed banner + retry/view logs; bảng lỗi core (E_* codes).
- **Gate:** cài API key masked → Credential Manager; đổi device override; lỗi hiển thị friendly + có error code + retry.

### Phase 11 — Optimization + Performance
- **Goal:** memory/chunking/parallelism; benchmark baseline.
- **Tasks:** memory management (gc + empty_cache, lazy load/unload); chunking translation concurrency giới hạn; benchmark script (1/10/30/60 phút: time, RAM, VRAM, CPU/GPU%); benchmark report.
- **Gate:** không OOM ở 8GB RAM CPU-only cho video 30 phút; số liệu baseline ghi vào report.

### Phase 12 — Testing
- **Goal:** test matrix toàn diện.
- **Tasks:** unit (Rust/Python/TS); integration (worker); AI pipeline (marker `ai`); **video golden test theo `GOLDEN_VIDEO_TEST.md`**; **translation quality benchmark theo `QUALITY_BENCHMARK.md` (Golden Translation Dataset)**; UI E2E (Playwright + Tauri driver); packaging test (install/uninstall, không cần runtime ngoài); regression.
- **Gate:** toàn bộ test xanh trên Windows + 1 GPU runner; E2E flow import→transcribe(mock)→translate(mock)→render chạy; golden video PASS checkpoint bắt buộc; translation đạt ngưỡng benchmark.

### Phase 13 — Packaging + Installer + Signing + Licensing
- **Goal:** installer NSIS + signing + licensing verify + updater artifacts.
- **Tasks:** PyInstaller onedir worker; CPU/GPU runtime add-on; FFmpeg bundle (LGPL-safe build có bảng license); Tauri bundler NSIS (hooks kill sidecar, WebView2 embedBootstrapper); code signing (OV cert, signtool, timestamp); **licensing verify toàn bộ LICENSE file + model card**; updater artifact (`setup.exe` + `.exe.sig`, `createUpdaterArtifacts: true`).
- **Gate:** cài trên Win10/11 mới (không có Python/Node/Rust/FFmpeg/CUDA) → chạy được; SmartScreen pass; không có dependency non-commercial; signing password test qua CI (tránh bug tauri#13485).

### Phase 14 — Beta + Auto Update
- **Goal:** auto-update production + beta test.
- **Tasks:** updater plugin (endpoints HTTPS, pubkey, passive installMode); manifest server (GitHub Releases → CDN); rollback test; beta checklist + bug triage.
- **Gate:** update từ bản cũ sang mới OK; rollback OK; không force-update giữa render.

### Phase 15 — Release
- **Goal:** release MVP.
- **Tasks:** release checklist (44 DoD của MASTER_PLAN); changelog; docs hoàn chỉnh; announce.
- **Gate:** toàn bộ DoD MVP pass.

---

## 4. THỨ TỰ CODING ƯU TIÊN (đường đi tới pipeline chạy sớm nhất)

Để có một pipeline end-to-end demo sớm, thứ tự task tối thiểu:

```text
001 → 003 → 004 → 005 → 006 → 008 → 010 → 011 → 009 → 012
→ 013 → 017 → 019 → 020 → 021 → 022 → 024 → 027 → 029
```

(Trong khi đó 002/007/016A-D/030/026 chạy song song theo phase; TASK-018 [OpenAI] = Post-MVP, không nằm trong path MVP — chỉ implement sau TASK-020 khi cần.) Chi tiết từng task ở `TASKS.md`.

---

## 5. RỦI RO CHÍNH TRONG TRIỂN KHAI (top 5 cần theo dõi)

| # | Rủi ro | Phase ảnh hưởng | Hành động |
|---|---|---|---|
| 1 | OOM/VRAM trên máy yếu | 5, 9, 11 | VRAM guard, CPU fallback, 1 job/lần — test 8GB sớm |
| 2 | whisper.cpp Vulkan crash AMD | 5 | 3 mitigations bắt buộc (beam ≤ 6, flash_attn off AMD, init single-thread) |
| 3 | Installer quá lớn (torch CUDA) | 13 | Tách CPU installer + GPU add-on |
| 4 | Sidecar NSIS reinstall không thay binary | 13, 14 | Installer hooks kill+delete, bump version |
| 5 | LLM pricing/model đổi (vd: Gemini 2.0 Flash shutdown) | 6, 15 | Provider abstraction + không hard-code; re-verify ở Phase 6 |

---

*Hết IMPLEMENTATION_ROADMAP.md — Task chi tiết ở TASKS.md.*
