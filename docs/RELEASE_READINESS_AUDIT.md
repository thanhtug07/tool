# Release Readiness Audit

**Date:** 2026-08-12
**Commit audited:** `b10a0dc` (all code is at `7e6fe5f`; later commits are docs only)
**Method:** strict audit against `MASTER_PLAN.md` (§1.2, §38.1a, §38.1b, §43, §44), `GOLDEN_VIDEO_TEST.md`, `QUALITY_BENCHMARK.md`, `TASKS.md`, `IMPLEMENTATION_ROADMAP.md`. No code changes made. Statuses used: `PASS` / `PARTIAL` / `BLOCKED` / `NOT IMPLEMENTED` / `NOT VERIFIED` / `PENDING` (evidence not yet produced).

---

## Executive Status

**NOT BETA READY.**

All 30 TASKS.md tasks are committed and every automated gate passes (worker 566 tests, Rust 144 tests, frontend 121 tests, typecheck/lint/format/build, clippy `-D warnings`). However, the tasks were completed as per-stage *services with tests*; the **MVP CORE vertical slice is not wired into a runnable product**. A user cannot import a video, run STT, translate, render, or export from the app today:

- The job runner is `NotWiredRunner` — every `job.submit` fails with `E_JOB_NOT_WIRED` (`src-tauri/src/services/job_service.rs:95-101`).
- The Projects page is a placeholder — there is no import UI, no project creation flow, and no UI calls `job.submit` at all.
- The worker HTTP surface exposes only `/health`, `/v1/stt/transcribe`, and the two export routes — no translate/subtitle/render endpoints.
- The mandatory quality gates (golden video, translation benchmark) have **no fixtures and were never executed**.
- The packaged installer ships neither the Python worker, FFmpeg, nor models — on a clean Windows machine the app opens but the pipeline cannot run.
- No real AI inference (STT with a real model, a real cloud translation call) has ever been executed; the `ai` marker tests are skipped.

In short: an impressive library of stage implementations with strong unit/integration coverage, but **not a product that can process a video end-to-end**, which is the definition of the MVP in MASTER_PLAN §38.1a.

---

## MVP Requirements Matrix

Source of truth: MASTER_PLAN §38.1a (MVP CORE — bắt buộc), §38.1b (MVP POLISH — bắt buộc trước release), §43 (Acceptance), §44 (DoD).

### MVP CORE (vertical slice)

| # | Requirement | Implementation | Test | Evidence | Status |
|---|---|---|---|---|---|
| 1 | Windows desktop app — installable, launchable | Tauri 2 shell (TASK-003), packaging built | build | `target/release/ai-video-localization.exe`, MSI + NSIS installer built; **never installed on a clean machine; packaged app cannot start the worker** | PARTIAL |
| 2 | Import video (MP4/MKV/MOV/AVI/WebM) | Only `projects` DB CRUD service (TASK-008); no import command, UI, or route | none | `src/pages/Projects/index.tsx` is a placeholder; no `import` API in `src/api/*` | NOT IMPLEMENTED |
| 3 | Video metadata detection (ffprobe) | `worker/src/services/media_service.py` probe (TASK-009) | unit + integration (real ffprobe on synthetic MP4/MKV/MOV/WebM fixtures) | `tests/integration/test_media_probe.py`; golden JSON fixtures | PASS (service) / NOT IMPLEMENTED (in app) |
| 4 | Audio extraction (16k mono) | `worker/src/services/audio_service.py` (TASK-012) | unit + integration with real ffmpeg, incl. cancel + no-audio + injection tests | `tests/integration/test_audio_service.py` | PASS (service) / NOT IMPLEMENTED (in app) |
| 5 | Local STT (segments + timestamps + confidence) | `stt_service.py` faster-whisper + whisper.cpp fallback (TASK-013/015) | unit only, **all mocks/test doubles; `ai` marker excluded** | `tests/unit/test_stt_service.py`, `test_stt_whisper_cpp.py`; no model downloaded, no real inference ever run | PARTIAL |
| 6 | Language detect + override | `stt_service` language param + detect | unit | same as above | PARTIAL |
| 7 | Contextual translation (cloud default) | `context_service.py`, `providers/` Gemini + local (TASK-017/019/020/021) | unit with MockProvider; real Gemini call skipped (no `GEMINI_API_KEY`) | `tests/unit/test_gemini_provider.py:239` `@pytest.mark.ai` skipped | PARTIAL |
| 8 | Translation QC + retry + hallucination | `quality_service.py` (TASK-022) | unit | `tests/unit/test_quality_service.py` | PASS (service) |
| 9 | Subtitle generation ASS/SRT/VTT | `subtitle_service.py` (TASK-024) | unit + integration (ffmpeg parse) | `tests/integration/test_subtitle_ffmpeg.py` | PASS (service) |
| 10 | Subtitle style (font/size/stroke/shadow/position) | ASS style in SubtitleEngine | unit | subtitle_service tests | PASS (service) |
| 11 | Burn-in render (libass + HW encode + CPU fallback) | `render_service.py` (TASK-027/028) | unit + integration with real ffmpeg (cancel, fallback, validation, watermark) | `tests/integration/test_render_ffmpeg.py` | PASS (service) |
| 12 | Export video + subtitle files | TASK-029 (worker + Rust IPC + UI) | unit + integration + Rust | `test_export_routes.py`, `test_render_ffmpeg.py` export tests, Rust `worker_client` tests | PASS (service + IPC) |
| 13 | Progress + ETA + cancel | services support; job system has cancel flags; render/audio cancel integration-tested | integration | `test_cancel_mid_render_cleans_up`, `test_cancel_before_start_aborts` | PARTIAL (no UI/progress wiring) |
| 14 | Error handling + retry (error table) | error codes defined; JobService retry logic | unit | job_service tests | PARTIAL (error table not user-visible end-to-end) |
| 15 | Job pipeline + logs | JobService persists state/events; **runner is `NotWiredRunner`** | unit (state machine) | `job_service.rs:95-101` returns `E_JOB_NOT_WIRED` for every job type | NOT IMPLEMENTED (execution) |

### MVP POLISH (bắt buộc trước release)

| Requirement | Status | Evidence |
|---|---|---|
| Subtitle editing UI | PASS | TASK-025, `SubtitleEditorView.tsx` + tests |
| Subtitle preview (video + overlay) | PASS | TASK-026, `PreviewView.tsx` + `VideoPreview.tsx` + tests |
| Glossary + translation memory | PASS | TASK-023, dictionary commands + UI + worker TM |
| Watermark (text + image) | PASS | TASK-028 + `WatermarkConfig.tsx` |
| Cache (STT/translation/render) | PASS | TASK-011, Rust + Python parity tests |
| Project save/load/resume | PARTIAL | CRUD + job resume unit-tested; resume of a real pipeline never exercised (pipeline cannot run) |
| Settings (AI/GPU/API masked/cache/privacy) | PASS | TASK-030 + `SettingsPage` |
| GPU detect + device override | PARTIAL | `hardware.py` + `hardware_probe.rs` unit-tested; not exercised on real NVIDIA/AMD/Intel hardware, no UI binding |
| Privacy Mode (local-first) | PARTIAL | setting persisted (TASK-030); **no code reads it to gate uploads** — enforcement only matters once the pipeline exists |

### DoD (MASTER_PLAN §44)

| Tier | Item | Status |
|---|---|---|
| T1 | Build + install on clean Win10/11 without Python/Node/Rust/FFmpeg/CUDA; clean uninstall | BLOCKED — installer built, never installed; packaged app lacks worker/FFmpeg/models |
| T1 | Test suite green: unit + integration + E2E + benchmark 1/10/30/60 min | BLOCKED — unit/integration green; no E2E, no benchmarks |
| T1 | No critical crash on cancel/OOM/API fail | PARTIAL — cancel tested in services; OOM/API-fail not tested on real paths |
| T1 | No security regression (keys only Credential Manager, no secret logs, no file fallback) | PASS (design + code) — see Security |
| T1 | SmartScreen pass (signed); auto-update + rollback | BLOCKED — unsigned; updater absent (post-MVP) |
| T2 | Pipeline E2E on 10-min Chinese→Vietnamese video | BLOCKED — pipeline not executable; no sample video |
| T2 | CPU-only + NVIDIA GPU | BLOCKED — never run on real hardware |
| T2 | Cache semantics (style→render only; edit→no re-STT) | PASS (unit/parity) / NOT VERIFIED (real flow) |
| T2 | Cancel mid-render cleans temp; resume doesn't re-run AI | PARTIAL — cancel tested; resume untested in real flow |
| T3 | STT golden checkpoint (timing ±200 ms, no missed segments) | BLOCKED — no fixture, never run |
| T3 | Translation score threshold on Golden Translation Dataset | BLOCKED — no dataset, never run |
| T3 | Subtitle readability (line-break policy, CPS, padding, preview≈render) | PARTIAL — policy unit-tested; real-video readability not validated |
| T3 | Video integrity (render validation) | PASS — integration-tested (resolution/FPS/audio/duration/burn-in) |
| T3 | Docs complete (README, ARCHITECTURE, DEVELOPMENT, AI/VIDEO/AUDIO_PIPELINE, DATABASE, API, SECURITY, LICENSING, TESTING, RELEASE) | NOT IMPLEMENTED — see Documentation |
| T3 | No non-commercial dependency in release; licensing table verified | NOT IMPLEMENTED — no LICENSING.md, no pip-licenses/cargo-deny whitelist, no LICENSE file |

---

## End-to-End Pipeline

Audited stage-by-stage (`Import → Analyze → Extract → STT → Translate → Subtitle → Edit → Preview → Render → Watermark → Export`).

| Stage | Implemented | Unit tested | Integration tested | Real media tested | Failure handling | CPU path | NVIDIA GPU path |
|---|---|---|---|---|---|---|---|
| Import (UI + command) | NOT IMPLEMENTED | — | — | — | — | — | — |
| Media analyze (ffprobe) | PASS (service) | PASS | PASS (real ffprobe) | PASS (synthetic fixtures; not golden) | PASS (corrupt files) | PASS | n/a |
| Audio extract | PASS (service) | PASS | PASS (real ffmpeg) | PASS (synthetic) | PASS (no-audio, cancel, injection) | PASS | n/a |
| STT | PASS (service) | PASS (mocks only) | NOT IMPLEMENTED | NOT VERIFIED (no model run) | PARTIAL (unit) | NOT VERIFIED (no real whisper run) | NOT VERIFIED |
| Translation (Gemini) | PASS (service) | PASS (MockProvider) | NOT IMPLEMENTED (ai skipped) | NOT VERIFIED (no API key) | PARTIAL (mock) | n/a | n/a |
| Translation (local llama.cpp) | PASS (service) | PASS (mock server) | NOT VERIFIED (no GGUF/binary) | NOT VERIFIED | PARTIAL | NOT VERIFIED | NOT VERIFIED |
| Subtitle generation | PASS (service) | PASS | PASS (ffmpeg parse) | PASS (synthetic) | PASS | n/a | n/a |
| Subtitle editing UI | PASS | PASS | PARTIAL (DB roundtrip tested) | — | PASS | n/a | n/a |
| Preview | PASS | PASS | — | PARTIAL (needs real video path) | PASS | n/a | n/a |
| Render (burn-in) | PASS (service) | PASS | PASS (real ffmpeg: HW/CPU encoders, cancel, fallback, validation) | PASS (synthetic clips) | PASS | PASS | NOT VERIFIED (no NVIDIA encoder tested) |
| Watermark | PASS | PASS | PASS (pixel-region checks) | PASS (synthetic) | PASS | n/a | n/a |
| Export + QC | PASS | PASS | PASS (ffprobe verify) | PASS (synthetic) | PASS | n/a | n/a |

**Conclusion:** the final third of the pipeline (subtitle → render → watermark → export) is genuinely implemented and integration-tested against real ffmpeg on synthetic media. The front half (import → STT → translate) is implemented as services but **never executed with real inputs**, and **nothing joins the stages into a runnable flow**. The job system that should orchestrate it has no executor wired.

---

## Golden Video Validation

- Requirement: MASTER_PLAN §38.1a quality gate — pipeline must PASS `GOLDEN_VIDEO_TEST.md` checkpoints (1–12) on a 10-minute Chinese→Vietnamese sample; §44 Tầng 3 requires timing ±200 ms and no missed segments.
- Evidence of the fixture: `GOLDEN_VIDEO_TEST.md` itself states **"Video mẫu chưa có → TODO — CREATE GOLDEN VIDEO FIXTURE"**. There is no `golden/` directory, no `worker/scripts/golden_video_test.py`, and no reference transcript.
- Real-media evidence: none. STT has never transcribed any media (no model, no run). The only media exercised are tiny synthetic ffmpeg clips in `worker/tests/fixtures/media/`.

**BLOCKED — real golden-video validation not executed.**

---

## Performance Validation

Requirements (MASTER_PLAN §4.1): 1 min (fast, frequent), 10 min (stable, no OOM), 30 min (stable, clear progress), 60 min (overnight safe), 2 h+ (streaming, never full-load); RAM/VRAM/disk targets; cache; cancel; resume.

| Requirement | Real measurement | Evidence |
|---|---|---|
| 1-minute pipeline time | NOT VERIFIED | no benchmark |
| 10-minute stability (no OOM) | NOT VERIFIED | no benchmark |
| 30-minute stability + progress | NOT VERIFIED | no benchmark |
| 60-minute overnight safety | NOT VERIFIED | no benchmark |
| 2 h+ streaming (never full-load) | NOT VERIFIED | no benchmark |
| RAM / VRAM measurements | NOT VERIFIED | VRAM guard logic unit-tested only (`test_stt_service` mocks) |
| Cache hit/miss real-flow timing | NOT VERIFIED | semantic tests only (Rust + Python parity) |
| Cancel (temp cleanup) | PASS | integration: `test_cancel_mid_render_cleans_up`, `test_cancel_before_start_aborts` |
| Resume (doesn't re-run AI) | NOT VERIFIED | state machine unit-tested; no executable pipeline to resume |

No benchmark scripts exist (`scripts/bench*` absent; `worker/scripts/golden_video_test.py` and `benchmark_translation.py` absent). DoD Tầng 1 "benchmark 1/10/30/60 phút" is unmet.

---

## Windows Packaging

| Item | Status | Evidence |
|---|---|---|
| Release exe | PASS | `target/release/ai-video-localization.exe` (10.4 MB) |
| MSI installer | PASS | `target/release/bundle/msi/AI Video Localization Studio_0.1.0_x64_en-US.msi` (4.1 MB) |
| NSIS installer | PASS | `target/release/bundle/nsis/AI Video Localization Studio_0.1.0_x64-setup.exe` (2.9 MB) |
| Installer smoke test (install/launch/uninstall) | PENDING | never installed on any machine |
| Clean Windows machine (no Python/Node/Rust/FFmpeg/CUDA) | BLOCKED | packaged app has no Python worker, no FFmpeg, no models |
| Worker startup after install | BLOCKED | `worker_manager.rs:766` — "Bundled (release) binary resolution is a later packaging phase"; spawns `python -m src.main` from PATH (`spawn_worker`), which a clean user machine lacks |
| FFmpeg availability after install | BLOCKED | `resolve_ffmpeg()` resolves `ffmpeg` via PATH or `FFMPEG_BIN`; `vendor/ffmpeg/` is empty; not bundled |
| Model availability after install | BLOCKED | models download at runtime from HF (TASK-016); no model UI, no download flow in app; `vendor/models/` empty |
| WebView2 | PARTIAL | no `webviewInstallMode` configured (default download-bootstrapper — needs internet on target); untested |
| Uninstaller | PENDING | NSIS default generated; never tested |

**Do not confuse artifact generation with installation validation.** Artifacts exist; installation on a real/clean machine has not happened and would not work for the pipeline today.

---

## Security

| Area | Status | Evidence |
|---|---|---|
| Credential Manager (keys) | PASS (design + code) | `SecretStore` via `keyring`; FIX #8 fail-safe (vault unavailable → save blocked, no file/crypto fallback); unit-tested with mock vault |
| Real Credential Manager roundtrip | NOT VERIFIED | tested against in-memory mock only — no real vault interaction on this machine |
| API keys in logs/DB/UI | PASS | keys never in DB, never logged, masked-only IPC (`AIz****wxyz`) |
| SQLite | PASS (design) | WAL, versioned migrations, UUID validation guards path traversal |
| Worker IPC | PASS (design) | loopback-only, per-session bearer token via stdin, token never in argv/logs |
| Tauri capabilities | PASS | `core:default` only (deny-by-default) |
| CSP | PASS | strict CSP in `tauri.conf.json`; no `unsafe-eval`/remote sources |
| Filesystem permissions | PASS (design) | no broad fs grants; export dir write-probe + atomic writes |
| Shell execution | PASS | argument arrays only; allowlists for ffmpeg/whisper-cli; `validate_input_path`; no `shell=True`/`os.system` |
| Temp files | PASS | cleanup integration-tested (render + audio cancel) |
| Model downloads | PASS (design) | SHA-256 verification before `ready` (TASK-016C); license field required |
| gitleaks scan (local) | NOT VERIFIED LOCALLY | gitleaks not installed on this machine; CI job exists but no local run/findings to cite |

Design is sound and consistent with MASTER_PLAN §20; however, **no security finding can be claimed from a local gitleaks run**, and the real OS-vault path is untested. Per audit rules, security is **NOT VERIFIED as a release gate**, not asserted PASS.

---

## Distribution

| Item | Status | Evidence |
|---|---|---|
| Code signing | BLOCKED | no OV cert; unsigned installer → SmartScreen warning; no signtool config |
| Version metadata | PASS | productName "AI Video Localization Studio", version 0.1.0, identifier set |
| Icons | PASS | build succeeded with `icons/` set |
| Uninstaller | PENDING | NSIS default; untested |
| Runtime dependencies (Python) | BLOCKED | not bundled (dev-mode spawn only) |
| FFmpeg packaging | BLOCKED | not bundled; PATH-only resolution |
| Model packaging/download | PARTIAL | registry/downloader/verifier/cache exist (worker-only, tested); no first-run download UX, no UI |
| Config migration | PARTIAL | versioned SQLite migrations (v1→v7 tested); settings schema is new and untested against real user data |
| First-run behavior | NOT IMPLEMENTED | no onboarding; Projects page empty placeholder; no API-key setup prompt; model download requires external steps |
| Updater | BLOCKED | post-MVP (T038); no plugin/endpoint/artifacts |

---

## Documentation

A new user must be able to install → launch → configure API → select provider → import video → translate → edit subtitles → render → export → troubleshoot, using docs alone.

| Doc (DoD list) | Exists | Notes |
|---|---|---|
| README.md | PASS | developer-oriented (npm/cargo/python quickstart); **no end-user install/use guide** |
| ARCHITECTURE (ARCHITECTURE_DECISION.md) | PASS | internal |
| DEVELOPMENT | NOT IMPLEMENTED | missing |
| AI/VIDEO/AUDIO_PIPELINE | NOT IMPLEMENTED | missing |
| DATABASE | NOT IMPLEMENTED | missing |
| API | NOT IMPLEMENTED | missing |
| SECURITY | NOT IMPLEMENTED | missing |
| LICENSING | NOT IMPLEMENTED | no `LICENSING.md`, no `LICENSE` file |
| TESTING | NOT IMPLEMENTED | missing |
| RELEASE | NOT IMPLEMENTED | missing |

There is **no user-facing guide** for the core flow (import → export) or troubleshooting, and the required DoD documentation set is largely absent. A non-developer user cannot complete the flow from docs today.

---

## Blocking Issues

### P0 — must fix before beta

1. **P0 — Pipeline is not executable end-to-end**
   - WHY: The MVP is defined as a runnable vertical slice (§38.1a). Today `job.submit` always fails (`E_JOB_NOT_WIRED`), no job executor is wired for any stage, there is no import flow/UI, and the worker has no translate/subtitle/render endpoints. A user cannot process a single video.
   - EVIDENCE: `src-tauri/src/services/job_service.rs:95-101` (`NotWiredRunner`); `src-tauri/src/lib.rs:104` (wired at startup); `src/pages/Projects/index.tsx` placeholder; `src/api/*` has no project.create/job.submit; `worker/src/api/routes.py` exposes only `/health`, `/v1/stt/transcribe`, `/v1/export/*`.
   - WHAT IS REQUIRED: wire JobRunner executors (transcribe/translate/subtitle/render) that call the worker over the authenticated loopback HTTP client; add worker routes for each stage; add import/project creation + job submission UI.
   - OWNER: development (agent + maintainer).

2. **P0 — Golden video validation not executed**
   - WHY: Mandatory quality gate (§38.1a, DoD Tầng 3). Without it there is no evidence STT timing/text quality meets the product promise.
   - EVIDENCE: `GOLDEN_VIDEO_TEST.md` header marks `TODO — CREATE GOLDEN VIDEO FIXTURE`; no `golden/` dir; no runner script; no STT run on any real audio.
   - WHAT IS REQUIRED: create the 10-min Chinese sample fixture + reference transcript (or licensed clip), run checkpoints 1–12, archive `golden_report.json`.
   - OWNER: maintainer + quality reviewer.

3. **P0 — Translation quality benchmark not executed**
   - WHY: Mandatory quality gate (§38.1a, DoD Tầng 3). No dataset, no threshold evidence, no provider regression protection.
   - EVIDENCE: `QUALITY_BENCHMARK.md` marks `TODO — CREATE GOLDEN TRANSLATION DATASET`; no `golden/translation/`; no `benchmark_translation.py`; `@pytest.mark.ai` Gemini test skipped (no key).
   - WHAT IS REQUIRED: build ≥50-case / 11-category dataset with reviewed references, implement runner, record baseline report.
   - OWNER: maintainer + bilingual reviewers.

4. **P0 — Packaged app cannot run the pipeline on a clean Windows machine**
   - WHY: DoD Tầng 1 requires install on clean Win10/11 with no Python/Node/Rust/FFmpeg/CUDA. The installer bundles none of the worker, FFmpeg, or models; the packaged worker spawn is explicitly deferred.
   - EVIDENCE: `worker_manager.rs:766` comment; `spawn_worker` resolves `python` from PATH; `resolve_ffmpeg` resolves via PATH; `vendor/ffmpeg/` and `vendor/models/` empty; `tauri.conf.json` has no `externalBin`/`resources`.
   - WHAT IS REQUIRED: PyInstaller worker bundle (or equivalent), FFmpeg bundle, sidecar externalBin wiring, installer hooks (kill/cleanup), WebView2 policy.
   - OWNER: development (packaging).

5. **P0 — No installer smoke test and no signing**
   - WHY: DoD Tầng 1: "Build + install Win10/11 mới; uninstall sạch" and "SmartScreen pass (signed)". Artifact generation ≠ installation validation.
   - EVIDENCE: installers exist (PASS) but were never installed; no OV certificate/signtool anywhere in the repo or environment.
   - WHAT IS REQUIRED: run install/launch/uninstall on a clean VM; obtain an OV code-signing certificate and sign the artifacts.
   - OWNER: maintainer (certificate = external decision).

### P1 — should fix before beta

6. **P1 — Real AI inference never executed (STT, cloud translation, whisper.cpp/llama.cpp)**
   - WHY: Without a single real run, model integration, VRAM guard, and output quality are unproven.
   - EVIDENCE: `ai` marker excluded in pytest config; `GEMINI_API_KEY`-gated test skipped; no model in cache; no whisper-cli/llama-server binary.
   - WHAT IS REQUIRED: one real STT run (model download + CPU, then NVIDIA if available), one real Gemini call, smoke the local LLM fallback.
   - OWNER: development.

7. **P1 — Performance benchmarks 1/10/30/60 min not run**
   - WHY: DoD Tầng 1 and §4.1 require them; there is no evidence of stability, OOM safety, or streaming behavior.
   - EVIDENCE: no bench scripts; no measurements.
   - WHAT IS REQUIRED: benchmark runs on 1/10/30/60-min media (CPU + GPU), record RAM/VRAM/time/progress.
   - OWNER: development.

8. **P1 — NVIDIA GPU path never tested**
   - WHY: Product DoD requires CPU + NVIDIA to work; encoder auto-pick logic exists but no hardware evidence.
   - EVIDENCE: render tests exercise software encoders; hardware detect is unit-tested with mocks only.
   - WHAT IS REQUIRED: run render/STT on a real NVIDIA machine (NVENC, CUDA, VRAM guard).
   - OWNER: maintainer (hardware access).

9. **P1 — Required documentation and licensing set missing**
   - WHY: DoD Tầng 3 lists 9 docs; only planning docs + README exist. Also no LICENSE file and no licensing verification (cargo-deny/pip-licenses, LGPL table).
   - EVIDENCE: root `ls *.md docs/*.md` shows no DEVELOPMENT/API/SECURITY/LICENSING/TESTING/RELEASE/DATABASE/AI_VIDEO_AUDIO_PIPELINE; `ls LICENSE*` → none; `MASTER_PLAN §21` checklist unverified.
   - WHAT IS REQUIRED: write the doc set; add LICENSE; run license audits and record results in LICENSING.md.
   - OWNER: maintainer.

10. **P1 — gitleaks security scan not verified locally**
    - WHY: cannot claim security PASS from absence of findings.
    - EVIDENCE: `gitleaks` not installed locally; CI job exists.
    - WHAT IS REQUIRED: run `gitleaks detect` on the repo (locally or via CI) and record the result.
    - OWNER: development.

## Non-Blocking Issues

- **P2 — Privacy mode is a stored setting only** — enforcement (no upload in local mode, explicit consent for cloud translation, no telemetry) must be wired when the pipeline lands.
- **P2 — Model management has no UI/first-run flow** — worker-side registry/downloader/verifier/cache exist and are tested; users need an in-app download/import path.
- **P2 — First-run experience / onboarding absent** — no guided API-key setup or model download; Projects page is an empty placeholder.
- **P2 — Auto-update (T038)** — explicitly post-MVP; not required for beta gate, but rollback test is listed in DoD Tầng 1 once it exists.
- **P2 — Resume/re-run semantics unproven in a real flow** — logic unit-tested; needs re-validation once the pipeline executes.
- **P2 — WebView2 install policy unconfigured** (default download-bootstrapper; needs internet) — decide embed vs. download for offline betas.
- **P2 — Docs/AUTONOMOUS_PROGRESS.md contains duplicated trailing lines** (cosmetic; generated file).

## Required Actions Before Beta

1. Wire the pipeline: job executors for all four stages over the authenticated worker HTTP client + worker routes for translate/subtitle/render + import/project UI + job submission UI. (P0)
2. Create the golden video fixture and run GOLDEN_VIDEO_TEST checkpoints 1–12; archive the report. (P0)
3. Create the golden translation dataset (≥50 cases / 11 categories) and run the benchmark to a recorded baseline. (P0)
4. Bundle the Python worker + FFmpeg into the installer; wire sidecar spawn for packaged builds; installer hooks; verify on a clean VM. (P0)
5. Run the installer smoke test (install → launch → worker up → uninstall clean) on clean Win10/11. (P0)
6. Obtain an OV code-signing certificate and sign the installer. (P0)
7. Execute at least one real STT run (CPU; then NVIDIA) and one real cloud translation call; record results. (P1)
8. Run 1/10/30/60-minute benchmarks (CPU + GPU) and record RAM/VRAM/time/progress. (P1)
9. Run gitleaks locally/CI and record the result. (P1)
10. Write the required docs (DEVELOPMENT, API, SECURITY, LICENSING, TESTING, RELEASE, DATABASE, AI/VIDEO/AUDIO_PIPELINE), add LICENSE, run cargo-deny + pip-licenses, record in LICENSING.md. (P1)
11. Re-run all layer gates after the above and re-audit. (all)

## Safe To Begin Beta?

**NO**

The MVP, as defined by MASTER_PLAN §38.1a/§43/§44, is not executable end-to-end: the pipeline has no wired job executor, no import flow, no translate/subtitle/render worker endpoints, the mandatory golden-video and translation-benchmark quality gates were never run, and the packaged installer cannot run the pipeline on a clean machine. These are P0s, not polish. Begin beta only after the Required Actions above are complete and a re-audit passes.
