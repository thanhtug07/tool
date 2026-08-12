# BUILD READINESS AUDIT

**Date:** 2026-08-12
**Refreshed:** 2026-08-12 — re-verified against the committed UI redesign (`c06d294`).
**Method:** source-level verification (Rust, Python worker, frontend, manifests, packaging). Every claim below was checked against the actual code, not documentation.

---

## 1. Current architecture

- **Shell:** Tauri 2 desktop app (`src-tauri/`), Rust core (`lib.rs` + commands + services), SQLite DB (WAL, versioned migrations).
- **AI worker:** separate Python 3.11+ process (`worker/`), FastAPI + uvicorn on `127.0.0.1` with a per-session bearer token passed over stdin (`src/main.py`, `src/api/routes.py`). Spawned/supervised by `WorkerManager` (crash-restart, health polling). In release builds the worker is a PyInstaller onedir bundle (`worker-dist/worker/worker.exe`) and FFmpeg is bundled under `vendor/ffmpeg/`.
- **Frontend:** React + Vite + Tailwind (`src/`), talks to Rust exclusively via typed IPC bridges (`src/api/*.ts`), single job store (`src/stores/jobs.tsx`).
- **Pipeline:** `PipelineRunner` (Rust) dispatches 4 job types to worker HTTP endpoints: `transcribe` (audio extract + STT), `translate`, `subtitle`, `render`. Jobs are persisted in SQLite; progress/cancel flow through `job:status` events + the worker's cancellation registry.

## 2. Current frontend flow

Four-area desktop shell (`src/App.tsx` + `src/components/layout/Sidebar.tsx`):

- **Dashboard** — overview only, not a processing workspace: system-status cards (worker / GPU / AI providers / storage, all real data), realtime processing with a real 5-stage checklist (extract audio → STT → translate → subtitles → render) derived from live job rows, recent projects table, processing history, quick actions.
- **Automation** (core) — dual Original/Result panels with a settings rail: essential options always visible (source/target language, voice "later" block, subtitle core + burn toggle, output format, disabled options for dubbing/music/logo-removal), provider + branding in a collapsed **Advanced options**. ⚡ AUTOMATE submits the 4-stage job plan (transcribe → translate → subtitle → render) via `job.submit`; realtime stage/progress/elapsed from the shared job store; honest "later" lines for voice/mixing/logo.
- **Tools** — category workspace (Video / Audio / Subtitle / AI); only backend-backed tools are enabled; tools that run inside the pipeline route to Automation; unbuilt tools are listed as "Planned — not in this build".
- **Settings** — five groups: General, AI providers, Audio & video, Storage, Advanced.

Completion screen: result player (Original/Result toggle), **Export** (worker QC), **Copy path**, **Reprocess**, **Edit subtitles**. No "Open folder" action — no reveal-in-Explorer backend command exists and the UI does not fake one. Subtitle editor, preview-with-overlay, dictionary/glossary and export views exist and are wired. Voice/dubbing, audio separation and logo removal are explicitly **not in this build** (no backend stages).

## 3. Current backend/Rust flow

`job.submit` → `JobService` → `PipelineRunner` per job type → authenticated `WorkerClient` → worker HTTP route → stage service → artifact in `{data}/projects/{id}/cache|output`. Cancel via `job.cancel` → worker cancel endpoint. Errors map to canonical envelopes (`E_*` codes, never stack traces).

## 4. Current Python worker flow

`src/main.py` (uvicorn) → `routes.py` (`/health`, `/v1/stt/transcribe`, `/v1/export/*`) + `pipeline.py` (`/v1/audio/extract`, `/v1/translate`, `/v1/subtitle`, `/v1/render`, `/v1/jobs/{id}/cancel`). Stage services: `audio_service` (FFmpeg 16k mono extract), `stt_service` (faster-whisper, VRAM guard, cancel), `translation_service` (TM + context engine + provider), `subtitle_service` (ASS/SRT), `render_service` (libass burn-in + watermark + QC), `media_service` (ffprobe probe), `quality_service` (retry/validation gate).

## 5. Current AI pipeline (implemented stages)

1. **Prepare/extract:** FFmpeg `-vn -ac 1 -ar 16000 -c:a pcm_s16le` → `cache/audio.wav`. REAL.
2. **STT:** faster-whisper (`large-v3` default, VRAM-guarded down to turbo/small/base/tiny), Silero VAD via `vad_filter=True`, per-segment progress + cancel. REAL (verified on real GPU + CPU in prior release audit).
3. **Translate:** provider abstraction — `mock` (offline, deterministic), `gemini` (real SDK, structured output), `local` (llama.cpp/OpenAI-compatible server). REAL code; `mock` proven; Gemini/local unproven against a live key/server.
4. **Subtitle:** rule-based ASS/SRT generation from transcript+translation (no ML model). REAL.
5. **Render:** libass burn-in of ASS + optional text/image watermark, encoder auto-pick (NVENC→libx264 fallback), output QC. REAL.
6. **Export:** copy + QC to user folder; subtitle export SRT/VTT. REAL.

## 6. Current providers

| Provider | Stage | Status |
|---|---|---|
| faster-whisper (CTranslate2) | STT | REAL — installed, real inference verified |
| whisper.cpp (ggml) | STT fallback | PARTIAL — code + unit tests, no bundled binary |
| Mock | Translation | REAL — offline pseudo-translation, works |
| Gemini (google-genai) | Translation | REAL code — **never run with a real key** |
| Local LLM (llama.cpp / OpenAI-compatible) | Translation | REAL code — **never run against a real server/GGUF** |
| TTS (any) | Dubbing | **NOT IMPLEMENTED** — `worker/src/services/providers/tts/` empty |
| Demucs | Audio separation | **NOT IMPLEMENTED** — post-MVP (T036) |
| OCR / logo removal | OCR, logo removal | **NOT IMPLEMENTED** — post-MVP |

## 7. Current local models

- The **only** model the current pipeline loads: faster-whisper CTranslate2 weights, downloaded at runtime from Hugging Face into the HF cache (`~/.cache/huggingface/hub/models--Systran--faster-whisper-*`). Verified: `faster-whisper 1.2.1` installed; **only `tiny` is cached on this machine** — `large-v3` (~3.1 GB) has NOT been downloaded yet.
- `models/manifest.json` (registry) + `worker/src/services/model_{registry,downloader,verifier,cache}.py` exist as an **unused** model-management layer: entries are unpinned (no checksums/sizes) and nothing in the pipeline reads it. STT does not consult it.

## 8. Current external APIs

- **Gemini API** (`api.gemini.*` settings + key from Windows Credential Manager) — translation only. No live-key verification on record.
- **Local LLM** (base URL + model path settings) — translation only. No live server verification on record.
- **Hugging Face** — model downloads at first use (faster-whisper). No other external calls. `connect-src` CSP is `ipc:` only (UI cannot call the network).

## 9. Current FFmpeg requirements

- FFmpeg + FFprobe required for: audio extract, render (burn-in/watermark), probe, export QC. Dev: PATH lookup. Release: bundled `vendor/ffmpeg/{ffmpeg,ffprobe}.exe`, injected via `FFMPEG_BIN`/`FFPROBE_BIN`. Verified present. Executable allowlist + argv-only execution (no shell).

## 10. Current model/weight requirements

Exactly one: a faster-whisper CTranslate2 model (default `large-v3`; VRAM guard can select turbo/small/base/tiny). Everything else (TTS, separation, OCR) is post-MVP and **not required by the current build**.

## 11. What is actually implemented

Import/analyze, audio extract, STT, translation (mock proven; Gemini/local coded), subtitle ASS/SRT, burn-in render, watermark, export+QC, jobs/progress/cancel, subtitle editor, preview overlay, dictionary/glossary, cache (STT/translation/render), settings incl. Credential-Manager keys, bundled worker + FFmpeg, 4-page UI, shared job/worker state.

## 12. What is partially implemented

- Translation: real Gemini/local provider code, but never executed against live credentials — unverified.
- whisper.cpp STT fallback: code complete, no binary/model in the repo.
- GPU encode: NVENC auto-pick exists; verified libx264 fallback; NVENC session unproven on this machine's GPU.
- Privacy-mode enforcement: setting stored, not read by the pipeline.

## 13. What is still mocked/stubbed

- `mock` translation provider is the only translation path proven end-to-end (it is a real provider, not a stub, but its output is pseudo-translation).
- **TTS / dubbing: absent** (no code, no provider dir content).
- **Audio separation / music preservation: absent.**
- **OCR / logo removal: absent.**
- Model registry/downloader/verifier/cache: implemented but **not wired** to any stage.

## 14. What prevents the app from processing a real 40-minute video

1. **P0 — No dubbing (TTS).** The demo goal ("final dubbed video") is impossible today: there is no TTS stage, no voice catalog, no audio-mix stage, no timing/alignment. The pipeline produces a translated + subtitled video, not a dubbed one.
2. **P0 — No audio separation / background-music preservation.** Not implemented.
3. **P1 — STT model not present on this machine** (`large-v3` absent; only `tiny` cached). First real run triggers a ~3 GB download, or must be forced to a smaller model.
4. **P1 — Translation unproven with real credentials.** Default provider is `mock`; Gemini key never exercised.
5. **P1 — 40-minute runtime unproven for STT.** faster-whisper large-v3 on CPU is slow (~0.2–0.3 RTF on this class of machine → 8–12 min); RAM/disk fine (streaming), but untested at 40 min on the real clip.
6. **P2 — Long-run progress/ETA accuracy** and resume on very long media not re-validated post-wiring.

## 15. What must be fixed before the first real end-to-end demo

Minimum for a **translated + subtitled** demo (achievable today):
1. Ensure a faster-whisper model is present (`large-v3`, or pass a smaller one) — one manual download command.
2. Run with `--provider mock` (no key) or configure a Gemini key in Settings (Credential Manager).
3. Use the demo runner (`scripts/demo/run_demo.py`) or the Automation UI.

For a **dubbed** demo (the stated product goal): the TTS stage + voice catalog + audio-mix stage must first be implemented (T037) — this is the single biggest gap and is a code task, not a configuration task.
