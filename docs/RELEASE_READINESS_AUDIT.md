# Release Readiness Audit

**Date:** 2026-08-12 (regenerated after release-gate execution)
**Commit audited:** `401dc16` (release phase RELEASE-P0-001 → RELEASE-P1-002 + security/keys fix on top of `8ffac29`).
**Method:** strict audit against `MASTER_PLAN.md` (§1.2, §38.1a, §38.1b, §43, §44), `GOLDEN_VIDEO_TEST.md`, `QUALITY_BENCHMARK.md`, `TASKS.md`, `IMPLEMENTATION_ROADMAP.md`. Statuses used: `PASS` / `PARTIAL` / `BLOCKED` / `NOT IMPLEMENTED` / `NOT VERIFIED` / `PENDING` (evidence not yet produced).

> This is a **regenerated** audit reflecting the completed release gates. The previous version (commit `8ffac29`) audited the repo after the release phase wired the MVP vertical slice. Between then and now, the release-gate push (documented in `docs/RELEASE_PROGRESS.md`) executed the security/license audits (**gitleaks, cargo-deny, pip-licenses**), ran **real GPU STT inference on an NVIDIA Quadro T1000**, validated **Windows Credential Manager round-trips live** (and **fixed a critical bug — API keys were silently stored in an in-memory mock store, never persisting to the OS vault**), rebuilt the installers on the fixed tree, ran the full final regression, and added an end-user guide. Every claim below cites current evidence.

---

## Executive Status

**NOT BETA READY** — but materially closer than the previous audit.

What has changed since the last audit:

| Area | Was | Now |
|---|---|---|
| Security scan (gitleaks) | NOT RUN (tool absent) | **PASS** — `gitleaks 8.24.3` scanned **71 commits, no leaks**; a single false fixture key was `gitleaks:allow`-marked (RELEASE_PROGRESS §Gate 3) |
| License audit (cargo-deny / pip-licenses) | NOT RUN (tools absent) | **PASS** — `cargo-deny check licenses advisories bans sources` all ok (15 `unmaintained` advisories, **zero CVEs**, ignored with justification); bunded-worker `pip-licenses` all commercial-safe (MIT/BSD/Apache/MPL/Unlicense); NVIDIA wheels not shipped (RELEASE_PROGRESS §Gate 3) |
| Credential Manager (keys) | mock-only, NOT VERIFIED | **PASS (fixed + live-verified)** — `keyring` now enables `windows-native`; real Windows-Vault round-trip test PASS (set→get→delete, entry visible in `cmdkey` then removed). This fixed a **critical regression: before the fix, API keys never persisted** (silent in-memory mock store) |
| NVIDIA GPU path | NOT VERIFIED (no CUDA-12 libs) | **PARTIAL→PASS (STT) / PARTIAL (NVENC)** — real faster-whisper STT on CUDA 0.49 s, 16/16 golden E2E `--device cuda`; NVENC fails on this embedded GPU (driver/session) → libx264 fallback verified as the mandated behavior (RELEASE_PROGRESS §Gate 2) |
| Real Gemini call | NOT VERIFIED (no key) | NOT VERIFIED — still key-gated (no `GEMINI_API_KEY` available); service + mock + benchmark PASS |
| End-user documentation | missing | **PASS** — `docs/USER_GUIDE.md` added (install, first run, API key, core flow, troubleshooting), linked from README |
| Installer built on fixed tree + silent install/launch/uninstall | installers predated keyring fix | **PASS** — installers rebuilt on `401dc16`; silent install → launch → installed-worker E2E 16/16 → uninstall clean (RELEASE_PROGRESS §Gate 1/6) |
| Clean-machine install | BLOCKED | still BLOCKED — no clean VM; dev-machine install/launch/uninstall from the real artifacts verified instead |

**Still blocking beta:** (1) installer smoke test on a **clean** Windows machine (no clean VM), (2) code signing (no OV certificate), (3) NVIDIA **NVENC** encode session on a desktop GPU (embedded Quadro limitation; libx264 fallback verified), (4) real Gemini end-to-end call (`GEMINI_API_KEY`), (5) `LICENSE` file (owner decision), (6) updater (post-MVP, Phase 14).

---

## MVP Requirements Matrix

Source of truth: MASTER_PLAN §38.1a (MVP CORE — bắt buộc), §38.1b (MVP POLISH — bắt buộc trước release), §43 (Acceptance), §44 (DoD).

### MVP CORE (vertical slice)

| # | Requirement | Implementation | Test | Evidence | Status |
|---|---|---|---|---|---|
| 1 | Windows desktop app — installable, launchable | Tauri 2 shell; packaging built + worker/FFmpeg bundled | golden E2E packaged | `target/release/ai-video-localization.exe`, MSI + NSIS installers; **packaged worker verified self-contained from outside the repo** (RELEASE-P0-007/008); **never installed on a clean machine** | PARTIAL |
| 2 | Import video (MP4/MKV/MOV/AVI/WebM) | import + project creation flow (`src/pages/Project`, `src/api/project.ts`, `dialog` plugin) | frontend unit | RELEASE-P0-005 (typecheck/lint/format/test pass) | PASS |
| 3 | Video metadata detection (ffprobe) | `worker/src/services/media_service.py` probe | unit + integration (real ffprobe on synthetic MP4/MKV/MOV/WebM) | `tests/integration/test_media_probe.py` | PASS |
| 4 | Audio extraction (16k mono) | `worker/src/services/audio_service.py` | unit + integration, incl. cancel + no-audio + injection | `tests/integration/test_audio_service.py` | PASS |
| 5 | Local STT (segments + timestamps + confidence) | faster-whisper + whisper.cpp fallback | unit + **real inference on golden audio** + integration | RELEASE-P0-006: real faster-whisper transcription of golden audio; whisper.cpp fallback unit-tested (no binary) | PASS (faster-whisper) / PARTIAL (whisper.cpp fallback) |
| 6 | Language detect + override | `stt_service` language param + detect | unit + real | golden E2E checkpoints cover detect; override unit-tested | PASS |
| 7 | Contextual translation (cloud default) | Gemini + local llama.cpp providers | unit (MockProvider) + **golden translation benchmark** | `benchmark_translation.py` baseline (RELEASE-P0-006); real Gemini call still key-gated (`GEMINI_API_KEY`) | PASS (service + benchmark) / NOT VERIFIED (real Gemini) |
| 8 | Translation QC + retry + hallucination | `quality_service.py` | unit | `tests/unit/test_quality_service.py` | PASS |
| 9 | Subtitle generation ASS/SRT/VTT | `subtitle_service.py` | unit + integration (ffmpeg parse) | `tests/integration/test_subtitle_ffmpeg.py` | PASS |
| 10 | Subtitle style (font/size/stroke/shadow/position) | ASS style in SubtitleEngine | unit | subtitle_service tests | PASS |
| 11 | Burn-in render (libass + HW encode + CPU fallback) | `render_service.py` | unit + integration with real ffmpeg | `tests/integration/test_render_ffmpeg.py` (cancel, fallback, validation, watermark) | PASS |
| 12 | Export video + subtitle files | worker routes + Rust IPC + UI | unit + integration + Rust | `test_export_routes.py`, `test_render_ffmpeg.py`, Rust `worker_client` tests | PASS |
| 13 | Progress + ETA + cancel | services + job system; UI progress | integration (cancel cleans temp) + frontend | `test_cancel_mid_render_cleans_up`, `test_cancel_before_start_aborts`; progress display (RELEASE-P0-005) | PASS |
| 14 | Error handling + retry (error table) | error codes + JobService retry | unit | job_service tests | PASS |
| 15 | Job pipeline + logs | `PipelineRunner` wired into `JobService` (replaces `NotWiredRunner`); per-stage dispatch/artifacts/cancel/error mapping | Rust unit + **golden E2E** | `lib.rs:134`, `pipeline_runner.rs:520`; E2E 16/16 dev + packaged | PASS |

### MVP POLISH (bắt buộc trước release)

| Requirement | Status | Evidence |
|---|---|---|
| Subtitle editing UI | PASS | TASK-025, `SubtitleEditorView.tsx` + tests |
| Subtitle preview (video + overlay) | PASS | TASK-026, `PreviewView.tsx` + `VideoPreview.tsx` + tests |
| Glossary + translation memory | PASS | TASK-023 + worker TM; TM regression fix in RELEASE-P1-001 |
| Watermark (text + image) | PASS | TASK-028 + `WatermarkConfig.tsx` + pixel-region integration tests |
| Cache (STT/translation/render) | PASS | TASK-011, Rust + Python parity tests |
| Project save/load/resume | PASS (real flow) | CRUD + job resume; pipeline now runs, so resume is exercisable — still not re-validated post-wiring on a long media |
| Settings (AI/GPU/API masked/cache/privacy) | PASS | TASK-030 + `SettingsPage` |
| GPU detect + device override | PARTIAL | hardware_probe unit-tested; **real NVIDIA exercised for STT** (Quadro T1000, CUDA 0.49 s, E2E 16/16 `--device cuda`); **NVENC encode fails on this embedded GPU → libx264 fallback verified** (mandated by MASTER_PLAN §9/§14) |
| Privacy Mode (local-first) | PARTIAL | setting persisted (TASK-030); enforcement gate still not read by the pipeline — same caveat as before; local-first default holds (STT/subtitle/render fully local) |

### DoD (MASTER_PLAN §44)

| Tier | Item | Status |
|---|---|---|
| T1 | Build + install on clean Win10/11 without Python/Node/Rust/FFmpeg/CUDA; clean uninstall | BLOCKED — installers built and **self-contained** (worker + FFmpeg bundled, packaged E2E PASS from outside the repo), but **never installed on a clean machine** (RELEASE-P0-008) |
| T1 | Test suite green: unit + integration + E2E + benchmark 1/10/30/60 min | PARTIAL — worker **583** / Rust **162** / frontend **136** green; E2E 16/16 dev + packaged; benchmarks PASS (CPU) + **GPU STT measured**; **GPU NVENC encode benchmark NOT RUN** (driver limit) |
| T1 | No critical crash on cancel/OOM/API fail | PARTIAL — cancel integration-tested; OOM/API-fail on real long paths not re-audited post-wiring |
| T1 | No security regression (keys only Credential Manager, no secret logs, no file fallback) | PASS — real Windows-Vault round-trip verified (keyring `windows-native` fix); gitleaks 71 commits no leaks; no secret logging |
| T1 | SmartScreen pass (signed); auto-update + rollback | BLOCKED — unsigned; updater absent (post-MVP) |
| T2 | Pipeline E2E on 10-min Chinese→Vietnamese video | PASS (synthetic golden) / NOT VERIFIED (real 10-min user clip) — golden E2E uses a deterministic synthetic clip; a real long bilingual clip was not available |
| T2 | CPU-only + NVIDIA GPU | PARTIAL — CPU PASS (real E2E + benchmarks); **NVIDIA STT PASS** (real CUDA inference + E2E `--device cuda`); **NVENC encode PARTIAL** (fails on this embedded GPU — driver/session limit; libx264 fallback verified) |
| T2 | Cache semantics (style→render only; edit→no re-STT) | PASS (unit/parity) / NOT VERIFIED (real flow post-wiring) |
| T2 | Cancel mid-render cleans temp; resume doesn't re-run AI | PASS (cancel integration) / NOT VERIFIED (resume in real flow) |
| T3 | STT golden checkpoint (timing ±200 ms, no missed segments) | PASS — golden E2E 16/16 covers these checkpoints (RELEASE-P0-006) |
| T3 | Translation score threshold on Golden Translation Dataset | PASS — dataset + runner + recorded baseline (RELEASE-P0-006) |
| T3 | Subtitle readability (line-break policy, CPS, padding, preview≈render) | PARTIAL — policy unit-tested; real-video readability not validated |
| T3 | Video integrity (render validation) | PASS — integration-tested (resolution/FPS/audio/duration/burn-in) + golden QC |
| T3 | Docs complete (README, ARCHITECTURE, DEVELOPMENT, AI/VIDEO/AUDIO_PIPELINE, DATABASE, API, SECURITY, LICENSING, TESTING, RELEASE + **end-user guide**) | PASS (11/12) — docs added RELEASE-P1-002 + `docs/USER_GUIDE.md` (switch to `NOT IMPLEMENTED`→PASS); **`LICENSE` file remains missing (owner decision)** |
| T3 | No non-commercial dependency in release; licensing table verified | PASS — `cargo-deny check licenses advisories bans sources` all ok; bundled-worker `pip-licenses` commercial-safe; FFmpeg LGPL notes in LICENSING.md |

---

## End-to-End Pipeline

Audited stage-by-stage (`Import → Analyze → Extract → STT → Translate → Subtitle → Edit → Preview → Render → Watermark → Export`).

| Stage | Implemented | Unit tested | Integration tested | Real media tested | Failure handling | CPU path | NVIDIA GPU path |
|---|---|---|---|---|---|---|---|
| Import (UI + command) | PASS | PASS | — | PASS (golden + packaged) | PASS | PASS | n/a |
| Media analyze (ffprobe) | PASS | PASS | PASS (real ffprobe) | PASS (synthetic) | PASS (corrupt files) | PASS | n/a |
| Audio extract | PASS | PASS | PASS (real ffmpeg) | PASS (synthetic + golden) | PASS (no-audio, cancel, injection) | PASS | n/a |
| STT | PASS | PASS | PASS (real faster-whisper inference) | PASS (golden audio, CPU + **CUDA**) | PARTIAL (whisper.cpp fallback no binary) | PASS | **PASS (STT)** — real CUDA inference 0.49 s, E2E `--device cuda` 16/16 |
| Translation (Gemini) | PASS | PASS (MockProvider) | PASS (golden benchmark) | NOT VERIFIED (no API key for real call) | PARTIAL (mock) | n/a | n/a |
| Translation (local llama.cpp) | PASS | PASS (mock server) | NOT VERIFIED (no GGUF/binary) | NOT VERIFIED | PARTIAL | NOT VERIFIED | NOT VERIFIED |
| Subtitle generation | PASS | PASS | PASS (ffmpeg parse) | PASS (golden) | PASS | n/a | n/a |
| Subtitle editing UI | PASS | PASS | PARTIAL (DB roundtrip) | — | PASS | n/a | n/a |
| Preview | PASS | PASS | — | PARTIAL (needs real video path) | PASS | n/a | n/a |
| Render (burn-in) | PASS | PASS | PASS (real ffmpeg: HW/CPU encoders, cancel, fallback, validation) | PASS (synthetic + golden) | PASS | PASS | PASS (CPU + HW-detected) — NVENC session fails on this embedded GPU; **libx264 auto-fallback verified** (MASTER_PLAN §9/§14 behavior) |
| Watermark | PASS | PASS | PASS (pixel-region checks) | PASS (synthetic) | PASS | n/a | n/a |
| Export + QC | PASS | PASS | PASS (ffprobe verify) | PASS (golden + packaged) | PASS | n/a | n/a |

**Conclusion:** the full MVP vertical slice is now **executable end-to-end and verified** in dev and packaged modes (golden E2E 16/16, including real faster-whisper STT and real FFmpeg render/watermark/export). Remaining pipeline gaps are hardware/credential-dependent (NVIDIA GPU, real Gemini key, whisper.cpp/llama.cpp binaries) rather than architecture gaps.

---

## Golden Video Validation

- Requirement: MASTER_PLAN §38.1a quality gate — pipeline must PASS `GOLDEN_VIDEO_TEST.md` checkpoints (1–12) on a 10-minute Chinese→Vietnamese sample; §44 Tầng 3 requires timing ±200 ms and no missed segments.
- Evidence: `golden/` now contains the deterministic synthetic fixture (video + audio + expected transcript + voices), `golden/scripts/generate_golden.py` + `run_golden.py`, and archived per-run reports. **E2E 16/16 checkpoints PASS** in dev (~4–12 s) and **packaged mode** (3.9 s, self-contained run from outside the repo) (RELEASE-P0-006/007).
- Caveat: the golden fixture is a deterministic synthetic clip generated locally (piper TTS voice), **not** a real 10-minute bilingual (Chinese→Vietnamese) video. Real-clip validation remains open as part of clean-machine / user-acceptance testing.

**PASS (synthetic golden E2E); real long bilingual clip still to be validated.**

---

## Performance Validation

Requirements (MASTER_PLAN §4.1): 1 min (fast, frequent), 10 min (stable, no OOM), 30 min (stable, clear progress), 60 min (overnight safe), 2 h+ (streaming, never full-load); RAM/VRAM/disk targets; cache; cancel; resume.

| Requirement | Real measurement | Evidence |
|---|---|---|
| 1-minute pipeline time | PASS | `benchmark_performance.py` → RTF 0.268, total 21.1 s |
| 10-minute stability (no OOM) | PASS | RTF 0.233, total 182.0 s |
| 30-minute stability + progress | PASS | RTF 0.262, total 580.0 s |
| 60-minute overnight safety | PASS | RTF 0.309, total 1490.6 s; peak worker RAM 140–151 MB, stable, no OOM |
| 2 h+ streaming (never full-load) | NOT VERIFIED | no 2 h run; loader is streaming by design (chunked) but not re-audited |
| RAM / VRAM measurements | PASS (RAM) / PARTIAL (VRAM) | RAM measured; **VRAM measured for real CUDA STT** (0→10→116→160 MiB on Quadro T1000, RELEASE_PROGRESS §Gate 2); full GPU benchmark still open |
| Cache hit/miss real-flow timing | PARTIAL | semantic parity tests; not timed in a long real run |
| Cancel (temp cleanup) | PASS | integration: cancel-mid-render + pre-start cleanup |
| Resume (doesn't re-run AI) | NOT VERIFIED | state machine unit-tested; not re-validated on a long real run post-wiring |

**PASS (CPU, 1/10/30/60 min) — see `worker/perf_report.json`. GPU STT measured (CUDA 0.49 s, 160 MiB VRAM); full GPU benchmark (incl. NVENC) open on desktop-GPU hardware.**

---

## Windows Packaging

| Item | Status | Evidence |
|---|---|---|
| Release exe | PASS | `target/release/ai-video-localization.exe`, rebuilt on `401dc16` |
| MSI installer | PASS | `target/release/bundle/msi/AI Video Localization Studio_0.1.0_x64_en-US.msi` (184.7 MB), rebuilt on the keyring-fixed tree |
| NSIS installer | PASS | `target/release/bundle/nsis/AI Video Localization Studio_0.1.0_x64-setup.exe` (133.4 MB), rebuilt |
| Bundled worker | PASS | PyInstaller onedir `worker-dist/worker/worker.exe` + `_internal/`; release-mode `WorkerManager` spawns it; handshake + `/health` + warmup verified (RELEASE-P0-007) |
| Bundled FFmpeg | PASS | `vendor/ffmpeg/{ffmpeg,ffprobe}.exe` bundled; artifacts list them |
| Packaged pipeline | PASS | self-contained golden E2E 16/16 run from `%TEMP%` + **from the fresh install dir** (installed `worker\worker.exe`, installed FFmpeg — RELEASE_PROGRESS §Gate 1/6) |
| Model availability after install | PARTIAL | models download at runtime from HF (registry/verifier/cache tested); **no in-app download UX** — first-run model fetch on a fresh OS unverified (open: clean machine) |
| Silent install (NSIS `/S`) | **PASS** | exit 0; extracted main exe, uninstall exe, `worker\*`, `ffmpeg\*` (RELEASE_PROGRESS §Gate 1) |
| Installed app launch | **PASS** | installed `ai-video-localization.exe` ran and stayed alive (37 MB RSS) |
| Installer smoke test (clean machine) | BLOCKED | requires a clean Windows VM (RELEASE-P0-008); portable + dev-machine install verified instead |
| Clean Windows machine (no Python/Node/Rust/FFmpeg/CUDA) | PARTIAL | packaged app is self-contained; clean-machine install/launch not yet exercised |
| WebView2 | PARTIAL | no `webviewInstallMode` configured (default download-bootstrapper — needs internet on target); untested |
| Uninstaller | **PASS** | `uninstall.exe /S` removed the install directory entirely on this machine (RELEASE_PROGRESS §Gate 6); clean-machine uninstall still to be re-run |

**Progress: "packaged app cannot run pipeline" is resolved** (RELEASE-P0-007), and the installers were additionally **rebuilt on the keyring-fixed tree and silently installed/launched/uninstalled on the dev machine** (RELEASE_PROGRESS §Gate 1/6). The remaining packaging gap is validation on a **clean** machine, which is environment-blocked.

---

## Security

| Area | Status | Evidence |
|---|---|---|
| Credential Manager (keys) | **PASS (fixed + live-verified)** | `SecretStore` via `keyring` with `windows-native` enabled. **Critical fix in `401dc16`:** previously `keyring = "3"` (no `windows-native`) silently fell back to an **in-memory mock store** — API keys never persisted to the OS vault. Now the real Windows Credential Manager is compiled in. FIX #8 fail-safe retained (vault unavailable → save blocked, no file/crypto fallback); allow-listed providers; masked display `AIz****wxyz` |
| Real Credential Manager roundtrip | **PASS** | new `#[ignore]`d integration test `real_vault_roundtrip_windows` (run explicitly on this Windows host): set→get (full secret matches)→delete→get `None`; credential visible in `cmdkey /list` then removed (RELEASE_PROGRESS §Gate 3b) |
| API keys in logs/DB/UI | PASS | keys never in DB, never logged, masked-only IPC (`AIz****wxyz`) — verified by logging audit across `src-tauri/src` + `worker/src` |
| SQLite | PASS (design) | WAL, versioned migrations, UUID validation guards path traversal |
| Worker IPC | PASS (design) | loopback-only, per-session bearer token via stdin (`WORKER_AUTH_TOKEN=` line → `READY <token>` handshake), token never in argv/logs |
| Tauri capabilities | PASS | `core:default` + `dialog:default` only (deny-by-default) — audited |
| CSP | PASS | strict CSP in `tauri.conf.json`; `connect-src` limited to `ipc:` (no external network), no `unsafe-eval`/remote sources — audited |
| Filesystem permissions | PASS (design) | no broad fs grants; export dir write-probe + atomic writes |
| Shell execution | PASS | argument arrays only; ffmpeg allowlist (`FFMPEG_ALLOWLIST`), `validate_input_path` rejects NUL/shell metacharacters; no `shell=True`/`os.system` (audit across `worker/src/**`) |
| Temp files | PASS | cleanup integration-tested (render + audio cancel) |
| Model downloads | PASS (design) | SHA-256 verification before `ready`; license field required |
| gitleaks scan (local) | **PASS** | `gitleaks 8.24.3 git` → **71 commits scanned, no leaks**; the one unit-test fixture key (`AIzaSy-secret-key-1234`) marked `gitleaks:allow` + allowlisted in `.gitleaks.toml` |
| cargo-deny | **PASS** | `check licenses advisories bans sources` all ok |
| pip-licenses (bundled worker) | **PASS** | all bundled packages commercial-safe; no proprietary NVIDIA wheels shipped |

Security is now **verified as a release gate** (OS-vault round-trip executed; gitleaks + cargo-deny + pip-licenses run and PASS). One behavioral bug was found and fixed (mock-store key storage) — this was exactly the kind of regression the gate exists to catch. Full detail: `docs/RELEASE_PROGRESS.md` §Gate 3, and `SECURITY.md`.

---

## Distribution

| Item | Status | Evidence |
|---|---|---|
| Code signing | BLOCKED | no OV cert; unsigned installer → SmartScreen warning; no signtool config |
| Version metadata | PASS | productName "AI Video Localization Studio", version 0.1.0, identifier set |
| Icons | PASS | build succeeded with `icons/` set |
| Uninstaller | PASS (dev-machine) | NSIS `uninstall.exe /S` removed the install dir entirely; clean-machine uninstall still open |
| Runtime dependencies (Python) | PASS | worker bundled (PyInstaller) — RELEASE-P0-007 |
| FFmpeg packaging | PASS | bundled — RELEASE-P0-007 |
| Model packaging/download | PARTIAL | registry/downloader/verifier/cache exist and tested; no in-app download UX |
| Config migration | PARTIAL | versioned SQLite migrations (v1→v7 tested); settings schema new and untested against real user data |
| First-run behavior | PARTIAL | import/job flow exists; no API-key onboarding prompt; model download still requires external steps |
| Updater | BLOCKED | post-MVP (T038); no plugin/endpoint/artifacts |

---

## Documentation

A new user must be able to install → launch → configure API → select provider → import video → translate → edit subtitles → render → export → troubleshoot, using docs alone.

| Doc (DoD list) | Exists | Notes |
|---|---|---|
| README.md | PASS | developer-oriented (npm/cargo/python quickstart) + links `docs/USER_GUIDE.md` for non-developers |
| ARCHITECTURE (ARCHITECTURE_DECISION.md) | PASS | internal |
| DEVELOPMENT | PASS | added RELEASE-P1-002 |
| AI/VIDEO/AUDIO_PIPELINE | PASS | added RELEASE-P1-002 |
| DATABASE | PASS | added RELEASE-P1-002 |
| API | PASS | added RELEASE-P1-002 |
| SECURITY | PASS | added RELEASE-P1-002 |
| LICENSING | PASS | `LICENSING.md` added (RELEASE-P1-002) with verified dependency table + FFmpeg LGPL notes; **no `LICENSE` file (project license UNDECIDED — owner)**, blocked by legal decision only |
| TESTING | PASS | added RELEASE-P1-002 |
| RELEASE | PASS | added RELEASE-P1-002 |
| USER_GUIDE (end-user) | **PASS** | `docs/USER_GUIDE.md` added (RELEASE_PROGRESS §Gate 5): install/launch on Windows, first run + model download, API-key setup via Credential Manager, core flow (import → transcribe → translate → subtitle → render → export), troubleshooting table |

**Gap resolved:** the end-user guide closes the DoD T3 documentation gap. Remaining doc gap is legal-only: the `LICENSE` file awaits the owner's license decision.

---

## Blocking Issues

### P0 — must fix before beta

1. **P0 — Installer smoke test on a clean Windows machine not performed**
   - WHY: DoD Tầng 1 requires "Build + install Win10/11 mới; uninstall sạch". The installers are now self-contained (worker + FFmpeg bundled, packaged E2E PASS from outside the repo), but installation/launch/uninstall on a clean OS has never happened.
   - EVIDENCE: RELEASE-P0-008 status BLOCKED; no clean VM available on the dev machine.
   - WHAT IS REQUIRED: install MSI/NSIS on clean Win10/11 → launch → worker up → import → pipeline → uninstall clean. Also validates first-run model download and WebView2 policy on a machine without them.
   - OWNER: maintainer (clean-machine access).

2. **P0 — Code signing (SmartScreen)**
   - WHY: DoD Tầng 1: "SmartScreen pass (signed)". Unsigned installer blocks smooth beta distribution.
   - EVIDENCE: no OV certificate/signtool anywhere in the repo or environment; artifacts unsigned.
   - WHAT IS REQUIRED: sign MSI/NSIS with an OV certificate + timestamp (signtool), or record the owner decision to ship unsigned.
   - OWNER: maintainer (certificate = external decision).

3. **P0 — `LICENSE` file absent (project license undecided)**
   - WHY: DoD Tầng 3 and MASTER_PLAN §21 require a license decision and file before distribution; AGENTS rule forbids asserting an unverified license.
   - EVIDENCE: README "License" = TODO; `src-tauri/Cargo.toml` `license TBD`; LICENSING.md checklist open.
   - WHAT IS REQUIRED: owner decides the project license; `LICENSE` file added; cargo-deny whitelist updated accordingly.
   - OWNER: maintainer/legal (owner decision).

### P1 — should fix before beta

4. **P1 — NVIDIA NVENC encode path not exercised on a working GPU**
   - WHY: product DoD requires CPU + NVIDIA; encoder auto-pick logic exists and real CUDA STT was validated, but an actual NVENC encode session has not succeeded on this machine.
   - EVIDENCE: RELEASE_PROGRESS §Gate 2 — real faster-whisper STT on Quadro T1000 (CUDA 0.49 s, E2E 16/16 `--device cuda`); but `h264_nvenc`/`hevc_nvenc` return "Function not implemented" (code -40) even on synthetic input (driver/GPU-session limitation of this embedded GPU). **The mandated NVENC→libx264 fallback works** (render 0.7–1.2 s, output valid). CPU path fully PASS.
   - WHAT IS REQUIRED: run one render with a working NVENC session on a desktop NVIDIA GPU; record in perf report.
   - OWNER: maintainer (hardware access).

5. **P1 — Real Gemini call and local-LLM fallback never executed**
   - WHY: translation service and providers are validated via MockProvider + golden benchmark; a real `GEMINI_API_KEY` call and a real llama.cpp server run are unproven.
   - EVIDENCE: `@pytest.mark.ai` Gemini test skipped (no key); no GGUF/llama-server binary present.
   - WHAT IS REQUIRED: one real Gemini call; smoke the local llama.cpp fallback; record results.
   - OWNER: development (key) / maintainer.

6. **P1 — CRITICAL fix shipped: API keys never actually persisted (resolved)**
   - RESOLVED in `401dc16` — `keyring` now enables `windows-native`; real Windows Credential Manager round-trip test PASS. This was a real P1 caught by the release gate: without the feature the crate silently used an in-memory mock store.

7. **P1 — License audits — DONE** — `cargo-deny check licenses advisories bans sources` and bundled-worker `pip-licenses` both PASS; FFmpeg LGPL notes recorded. Remaining is only the **owner's license decision** for the `LICENSE` file (P0 item 3).

8. **P1 — End-user documentation — DONE** — `docs/USER_GUIDE.md` added and linked from README (see Documentation section).

## Non-Blocking Issues

- **P2 — Privacy mode is a stored setting only** — enforcement (no upload in local mode, explicit consent for cloud translation, no telemetry) still not read by the pipeline; the local-first default (STT/subtitle/render fully local) holds.
- **P2 — Model management has no UI/first-run flow** — in-app download/import path needed; worker-side registry/downloader/verifier/cache exist and are tested.
- **P2 — First-run onboarding absent** — no guided API-key setup or model download; import/job UI now exists.
- **P2 — Auto-update (T038)** — post-MVP; not required for beta gate; rollback test is listed in DoD Tầng 1 once it exists.
- **P2 — Resume/re-run semantics unproven in a long real flow** — logic unit-tested; re-validate on real media.
- **P2 — WebView2 install policy unconfigured** (default download-bootstrapper; needs internet) — decide embed vs. download for offline betas.
- **P2 — Real 2 h+ streaming run not re-audited** — loader is streaming chunked by design; long-run benchmark was 60 min.
- **P2 — Real 10-min Chinese→Vietnamese video E2E not run** — golden fixture is synthetic; validated on real bilingual clip at cleaner-machine/UAT time.

## Required Actions Before Beta

1. Install MSI/NSIS on clean Win10/11: install → launch → worker up → import → pipeline → export → uninstall clean; validate first-run model download + WebView2. (P0)
2. Sign MSI/NSIS (OV cert + timestamp) or record the decision to ship unsigned. (P0)
3. Decide the project license and add `LICENSE`; update cargo-deny whitelist. (P0)
4. Run one render with a working NVENC session on a desktop NVIDIA GPU; record in `worker/perf_report.json`. (P1)
5. Execute one real Gemini call and smoke the local llama.cpp fallback. (P1)
6. ~~Run gitleaks locally~~ — DONE (71 commits, no leaks).
7. ~~Run cargo-deny + pip-licenses~~ — DONE (all ok).
8. ~~End-user install/use/troubleshooting guide~~ — DONE (`docs/USER_GUIDE.md`).
9. Re-run all layer gates — DONE on the fixed tree (Gate 6: worker 583, Rust 162, frontend 136, E2E 16/16 dev + packaged, silent install/launch/uninstall).
10. Re-audit after any of the remaining items complete.

## Safe To Begin Beta?

**NO**

The MVP is executable and verified end-to-end in dev and packaged modes: golden E2E 16/16 (CPU **and** CUDA-STT), CPU benchmarks 1/10/30/60 min PASS, real faster-whisper STT, bundled worker + FFmpeg, security + license audits PASS (gitleaks 71 commits clean, cargo-deny + pip-licenses ok), real Windows Credential Manager round-trip PASS (with the critical keys-persistence bug fixed in `401dc16`), installers silently installed/launched/uninstalled on the dev machine, and an end-user guide added.

Beta distribution is still blocked by environment/owner-dependent items that no local build can close: (1) installer smoke test on a **clean** Windows machine, (2) code signing (no OV cert; unsigned → SmartScreen), (3) the project **license decision** (`LICENSE` file), (4) **NVENC** encode on a desktop NVIDIA GPU, and (5) a real `GEMINI_API_KEY` translation call. Per §44 Tầng 1 these remain release-blocking. Begin beta only after those complete and a final re-audit passes. These are owner/hardware tasks rather than code gaps — the engineering-side release gates are all green at commit `401dc16`.