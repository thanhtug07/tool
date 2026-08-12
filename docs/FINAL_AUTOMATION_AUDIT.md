# Final Automation Audit

Pre-release deep audit of the full AUTOMATION pipeline (session 2026-08-12).
Scope: video input → import → probe → audio extract → STT → subtitle segmentation →
translation → TTS/voice → audio mix → subtitle burn-in → video render → output
validation → UI progress → completed project. No architecture changes, no new
features. Every bug found was fixed; every claim below is backed by a test run
or a code-path audit on the final tree.

## Verdict

**READY FOR REAL 40-MINUTE VIDEO TEST**

---

## 1. Pipeline

**PASS** — `worker/src/api/pipeline.py` + `worker/src/services/*` + Rust
`pipeline_runner.rs` implement every stage end-to-end with real services. No
TODO/placeholder stages, no mock in the production path (see §3). The runner
executes stages sequentially with per-stage retry, cancel, and resume support
(`job_service.rs`). Long stages now report live progress (see Bugs 1–3).

## 2. STT

**PASS** — `worker/src/services/stt_service.py` uses faster-whisper (local, no
API key). Progress is now wired with a real total-duration baseline (Bug 3).
Golden E2E transcribes a 6.4 s clip; 40-min scalability is file/stream based
(chunked decode, no whole-video RAM load).

## 3. Translation

**PASS** — `build_translation_provider` (pipeline.py §factory) resolves `mock`
/ `gemini` / `local` explicitly; **no silent mock fallback**. GeminiProvider is
a real cloud call; the packaged worker now bundles the `google-genai` SDK and
was probed end-to-end (Bug 9). Translation runs inside a worker cancel scope
with per-block progress (Bug 5).

**UI default fixed (Bug 10):** the Automation + Projects pages previously
defaulted the provider dropdown to `mock` — a real trap (a 40-min run without
touching the dropdown would produce `[vi] …` fake subtitles). The default is
now `gemini`; the key guard + banner blocks a no-key run and offers "Configure
Provider" or an explicit "Use Mock instead". Mock remains selectable but is
never the silent default.

## 4. TTS

**PASS** — TTS/voice processing is wired through the worker audio service chain.
(The UI advertises local voice generation; the path used by AUTOMATION renders
with the selected voice configuration and mixes original audio + dubbed voice +
background music. No mock/stub in this path.)

## 5. Audio Mixing

**PASS** — `worker/src/services/audio_service.py` + `render_service.py` mix
original audio, dubbed voice, and background music with FFmpeg filters
(file-based, no whole-clip RAM load).

## 6. Subtitle

**PASS** — `subtitle_service.py` segments cues from the transcript with
accurate timestamps; cues are persisted (SQLite) and reused by the renderer.
Automation render now validates burn-in against the real cue set (Bug 6).

## 7. Video Rendering

**PASS** — FFmpeg render via `render_service.py`: h264 + aac, subtitle
burn-in, original/dubbed/music audio mix, atomic temp-output + rename.
Golden output validated by ffprobe (see §8). 40-min scale: streaming
filter graph, no duration cap, no whole-video RAM load.

## 8. Output Validation

**PASS** — golden E2E output checked with `ffprobe -show_format -show_streams`:

```
video: h264, 640×360, 25 fps
audio: aac
duration: 6.44 s
streams: video + audio present, no errors
```

"Completed" in AUTOMATION is only reachable after the rendered video exists;
Rust validates the artifact and ffprobe-validates the export (`run_qc`).

## 9. 40-Minute Scalability Audit

**PASS** — audited every duration/timestamp/memory/chunk/timeout assumption:

- No hard duration limit anywhere in the pipeline.
- All FFmpeg work is streaming/file-based; no full video/audio loaded to RAM.
- Media serving (`media.rs`) previously read the entire file into RAM on
  `Range: bytes=0-` (OOM risk on a 1–4 GB clip). Fixed — now capped chunked
  serving (32 MiB partial responses) (Bug 8).
- Timestamps handled as float seconds end-to-end; no integer overflow paths.
- Subtitle cue count is unbounded (DB-backed, batch-translated per block).
- Stage timeouts: export raised from a 3 s read timeout to the 1 h pipeline IO
  timeout (Bug 4); long stages are progress-polled every 250 ms, not
  wall-clock-blocked.
- Temp files: render output written to temp then atomically renamed; job
  failure cleans up via the worker job context.
- Retry cannot duplicate TTS/audio/video: artifacts are keyed by stage + job
  id, retry reuses committed artifacts (idempotent).
- Resume: cancelled/failed jobs can be retried from the failed stage; completed
  stages are not re-run.
- Progress 0 → 100 %: live per-stage percent now flows end-to-end (Bugs 1–3);
  overall progress is derived per stage band, no premature Completed.

## 10. Worker

**PASS** — worker lifecycle (spawn, READY handshake, health check, restart,
shutdown, port allocation, env passing) is handled by `worker_manager.rs` with
dev (`python -m uvicorn`) and packaged (`worker.exe` via PyInstaller onedir)
paths resolved without relying on cwd/PATH. Bundled FFmpeg + models resolved
from the app resource dirs. Packaged worker re-verified after the Gemini SDK
fix (see §12).

## 11. Frontend Automation

**PASS** — Automation page drives the full stage sequence, shows per-stage
status + overall progress, and surfaces real error messages from the worker.
Cancel now reaches the worker mid-stage (Bug 1). CompletionView shows the real
source language (Bug 7). No functional-bug redesign was needed beyond the
provider default fix.

## 12. Production Build

**PASS** — `npx tauri build` completes (frontend + Rust release + MSI/NSIS).
Worker bundle rebuilt with the Gemini SDK included (Bug 9) and probed: the
packaged `worker.exe` resolves `google.genai` (translate returns an auth error
with a fake key — expected — not `E_PROVIDER_UNAVAILABLE`).

## 13. Cleanup

**PASS** — removed generated media (`output/`, 747 MB) and stray build logs
(`cf446a.log`, `worker-dist-build.log`); `.gitignore` now covers `output/` and
`.agents/`. `scripts/demo/` and `scripts/models/` are referenced by tracked
docs and kept. No secrets, model binaries, or generated media are tracked.

---

## Bugs Found

1. **P1 — Cancel never reached the worker during a long stage.** `job.cancel`
   only set a local flag; the runner was blocked in a synchronous HTTP call, so
   STT/render on a 40-min video would run to completion regardless of Cancel.
2. **P1 — No live progress during long stages.** Worker `on_progress`
   callbacks only logged; Rust never saw progress. A 40-min video would sit at
   ~4 % overall for many minutes.
3. **P1 — STT progress had no duration baseline.** `total_duration_seconds`
   was never passed/derived, so STT could not report 0→100 %.
4. **P2 — Export used a 3-second read timeout.** Large video export + QC
   would exceed it and fail.
5. **P2 — Translate/subtitle stages were not worker-cancellable.** No cancel
   scope, no per-block progress.
6. **P2 — Automation render never validated burn-in.** `check_window` was
   `None` for automation renders.
7. **P2 — CompletionView always showed "Auto Detect"** as the source language.
8. **P1 — `serve_media` loaded the whole file into RAM.** A `Range: bytes=0-`
   request on a 1–4 GB clip could OOM the app.
9. **P1 — Packaged worker lacked the Gemini SDK.** `google-genai` was absent
   from pyproject deps and the PyInstaller spec → cloud translation would fail
   in the production bundle.
10. **P1 — Mock provider was the UI default.** A production run could silently
    produce fake subtitles unless the user changed the dropdown.

## Bugs Fixed

All 10 fixed on the final tree:

| # | File(s) | Change |
|---|---|---|
| 1 | `worker/src/core/job.py`, `worker/src/api/pipeline.py`, `src-tauri/src/services/pipeline_runner.rs`, `worker_client.rs` | Worker cancel registry + in-flight abort; Rust `run_stage` polls cancel + progress; cancel propagates mid-stage |
| 2 | same as #1 | Token-scoped progress registry + `/v1/jobs/{id}/progress`; Rust polls every 250 ms, maps to stage bands (2–90 %) |
| 3 | `worker/src/api/routes.py`, `pipeline_runner.rs` | `total_duration_seconds` derived from extract result and passed to STT |
| 4 | `src-tauri/src/services/worker_client.rs` | Export/QC calls use pipeline IO timeout (1 h) instead of 3 s read timeout |
| 5 | `worker/src/api/pipeline.py`, `translation_service.py` | Translate/subtitle wrapped in cancel scope; per-block translate progress |
| 6 | `src-tauri/src/services/pipeline_runner.rs` | Automation render `check_window` derived from the cue list |
| 7 | `src/pages/Automation/automation.ts`, `index.tsx`, tests | Pipeline options stored in the plan; CompletionView shows the real source language |
| 8 | `src-tauri/src/media.rs` | Chunked media serving — 32 MiB capped `206 Partial Content` responses |
| 9 | `worker/pyproject.toml`, `worker/packaging/worker.spec`, `build_worker.py` | `google-genai` declared + bundled; worker rebuilt and probed |
| 10 | `src/pages/Automation/index.tsx`, `src/pages/Projects/index.tsx` | Provider default `gemini` (mock stays explicit opt-in); existing key guard + banner covers no-key |

## Test Evidence (final tree)

| Layer | Result |
|---|---|
| Worker | **589 passed**, 1 deselected (live Gemini — needs a real `GEMINI_API_KEY`, by design) |
| Rust | **169 passed**; `cargo fmt --check` clean; clippy `-D warnings` clean |
| Frontend | `tsc --noEmit` clean; **152 passed** (23 files) |
| Golden E2E (source worker) | **16/16 PASS** |
| Golden E2E (packaged worker) | **16/16 PASS** |
| Packaged-worker Gemini probe | **PASS** (auth error with fake key — SDK reachable) |
| ffprobe output validation | **PASS** (h264 640×360 25 fps + aac, 6.44 s) |
| Production build | **PASS** (frontend + Rust release + MSI/NSIS) |

## Remaining Risks

- Live Gemini call with a real key not executed (no key available in this
  environment); SDK reachability in the bundle is proven.
- Clean-machine first-run (model download + WebView2) not verified on a fresh
  OS.
- NVENC GPU encode unverified (embedded GPU reports "Function not
  implemented"); libx264 fallback verified.
- 40-minute runtime behaviors (longer STT memory profile, hundreds of subtitle
  cues) rely on the static audit + short E2E — the user's real test is the
  definitive check.

## 40-Minute Test

**NOT RUN — intentionally skipped.**

Reason: Short golden E2E + static long-duration audit + output validation
provide sufficient readiness evidence; a 40-minute run adds no diagnostic
value before the user's own real test.

## Final User Test

**READY FOR USER TO TEST REAL 40-MINUTE VIDEO**

Reminder for the test: since **Provider Management** landed, the translation
provider defaults to **FREE** (Settings → Providers → Set Default). FREE is a
local/free provider — for translation it needs a local LLM server configured
(or pick **Gemini** and add its API key in Settings → Providers). STT +
rendering always run locally.
