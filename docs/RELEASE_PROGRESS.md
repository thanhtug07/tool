# RELEASE_PROGRESS.md

Live release-gate log for the beta-readiness push (session started 2026-08-12).
Each gate records: status (`PASS`/`PARTIAL`/`NOT_RUN`/`NOT_VERIFIED`/`BLOCKED`),
concrete evidence, next action. Companion docs: `RELEASE_READINESS_AUDIT.md`,
`docs/AUTONOMOUS_PROGRESS.md`, `RELEASE.md`, `TESTING.md`, `SECURITY.md`.

---

## Gate 1 — Clean-machine install smoke test

**Status: PARTIAL** (verified on the dev machine from the *actual installer*; a true clean VM install remains BLOCKED).

Environment: Dell Precision 5550 (Win11, NVIDIA Quadro T1000 4 GB), no clean VM available.

What was done (all from the packaged artifacts, not `cargo run`):

1. **Packaged worker was stale — rebuilt.** `worker-dist/worker/worker.exe` predated the RELEASE-P1-001 translation fix (350c611). Rebuilt via `py -3.13 worker/packaging/build_worker.py` → `worker-dist/worker/worker.exe` (276 MB onedir; fresh faster-whisper 1.2.1 / ctranslate2 4.8.1 / onnxruntime 1.20.0; no nvidia/CPU-only bundles pulled in).
2. **Installers rebuilt.** `npx tauri build` (with cargo on PATH) produced fresh:
   - `target/release/bundle/msi/AI Video Localization Studio_0.1.0_x64_en-US.msi` (184.7 MB)
   - `target/release/bundle/nsis/AI Video Localization Studio_0.1.0_x64-setup.exe` (133.4 MB)
   - `target/release/ai-video-localization.exe` (main exe)
3. **Silent install.** `setup.exe /S /D=<temp>` → exit 0; extracted `ai-video-localization.exe`, `uninstall.exe`, `worker\worker.exe`, `worker\_internal\*`, `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`.
4. **Launch.** Installed `ai-video-localization.exe` ran and stayed alive (37 MB RSS after 6 s).
5. **Bundled worker + FFmpeg from install dir.** Spawned the installed `worker\worker.exe` (outside the repo) with `FFMPEG_BIN/FFPROBE_BIN` → install-dir binaries:
   - READY handshake: PASS (`READY <token>` echoes the stdin `WORKER_AUTH_TOKEN`)
   - `GET /health` → `{"status":"ok","version":"0.1.0"}`
   - Real STT on golden audio (`/v1/stt/transcribe`, tiny, cpu): HTTP 200, 1 segment, correct text.
6. **Golden E2E via installed worker** (`run_golden.py --worker-exe <install>\worker\worker.exe`): **16/16 PASS in 3.6 s** (audio extract → real STT → translate → subtitle → render → export QC).

Findings:
- Worker startup needs a normal Windows environment (full env inherited). Spawned with a *minimal* env, `asyncio` fails `WinError 10106` (Winsock provider) — a harness artifact, not a product bug; the app/WorkerManager inherits the full env.
- First-run model download on a *fresh* OS (no HF cache) was **not** testable here (cache already present) — still open.

Next action: run install → launch → pipeline → uninstall on a clean Win10/11 VM (no Python/Node/Rust/FFmpeg/CUDA). Owner: maintainer (hardware access).

---

## Gate 2 — NVIDIA GPU validation

**Status: PASS (CUDA STT inference, CPU-first packaged default) / PARTIAL (NVENC encode fails on this GPU; libx264 fallback verified).**

Evidence (real hardware: **Quadro T1000, 4096 MiB, driver 582.16, CC 7.5**; CUDA Toolkit v11.8 installed, so CUDA-12 runtime libs for ctranslate2 were provisioned via pip `nvidia-cublas-cu12` + `nvidia-cuda-runtime-cu12`):

- `ctranslate2.get_cuda_device_count()` → **1 device** (was reported before as "device count 1 but cublas64_12.dll missing" — now resolved by adding the CUDA-12 runtime).
- Real faster-whisper inference (`tiny`, `device=cuda`, int8) on golden audio: **0.49 s**, correct text, GPU util sampled during inference **0→6→50 %**, VRAM used **0→10→116→160 MiB**.
- Golden E2E **`--device cuda`**: **16/16 PASS in 5.8 s** (source worker) and **16/16 PASS in 9.5 s** (fresh packaged worker) — real STT on CUDA, rest CPU.
- **NVENC encode fails on this machine** (`h264_nvenc`/`hevc_nvenc`: "Function not implemented", code -40) even on synthetic input, both system and vendored FFmpeg 9.0. The render service detects NVENC but the encoder session cannot be opened → **automatic fallback to libx264 works** (golden render 0.7–1.2 s, output valid). NVDEC (`-hwaccel cuda`) decode works. This is a driver/GPU-session limitation of this embedded GPU, not a code path; MASTER_PLAN §9/§14 mandates exactly this NVENC→libx264 fallback.
- Packaged worker bundles `ctranslate2\cudnn64_9.dll` but **not** `cublas64_12.dll`. Per MASTER_PLAN §32 (CPU-first default; "AI add-on download optional"), the CUDA runtime is intentionally **not** bundled — GPU works when the CUDA-12 runtime is present. Documented, not changed (no scope/architecture change without approval).

Next action: re-run STT + render on a machine with a working NVENC session (desktop NVIDIA GPU) to close the encode gap; record in `worker/perf_report.json`. Owner: maintainer.

---

## Gate 3 — Security verification

**Status: PASS** (all sub-gates green; one critical finding fixed: API keys were never actually persisted to Credential Manager — see 3b).

Scope: gitleaks, cargo-deny, pip-licenses, secret scanning, shell-execution audit, Tauri CSP/capabilities, Credential Manager, logging audit, FFmpeg argument safety, dependency/license audit.

### 3a. Dependencies & tooling (done)

- **cargo-deny 0.20.2** (installed via `cargo install`): `licenses`, `bans`, `sources` all OK. `advisories` failed only on **15 `unmaintained` advisories — zero CVEs**: RUSTSEC-2024-0411..0420 (gtk-rs GTK3 bindings, **Linux-only**, not shipped on Windows), RUSTSEC-2024-0370 (`proc-macro-error`, dev/build-time), RUSTSEC-2025-0075/0080/0081/0098/0100 (`rust-unic`/`unic-*`, transitive of Tauri, no RCE/USB-C tiers). All 15 ignored in `deny.toml` with written justification; full `cargo deny check licenses advisories bans sources` now **PASSES**. (Fixes: `deny.toml` + `.github/workflows/ci.yml` args.)
- **gitleaks 8.24.3** (installed from GitHub release zip): full `git` history scan (70 commits) → **1 finding**: a fake fixture value `AIzaSy-secret-key-1234` in `src/api/settings.test.ts:42` (commit 7e6fe5f, TASK-030). Confirmed fake (test-only string literal, never sent anywhere). Inline `gitleaks:allow` markers added + `.gitleaks.toml` allowlist regex → re-run **no leaks found**.
- **pip-licenses 5.5.5**: bundled dist-info set (16 packages: attrs, click, cryptography, ctranslate2, email_validator, faster_whisper, itsdangerous, jsonschema, markupsafe, numpy, pydantic, pyreadline3, tokenizers, tqdm, websockets, werkzeug) all **commercial-safe** (MIT / BSD / Apache-2.0 / MPL-2.0 / Unlicense). NVIDIA proprietary wheels NOT bundled (dev-env only). PyInstaller (GPLv2) is a build-time tool, not shipped runtime.
- **Static shell-execution audit**: no `shell=True` / `os.system()` anywhere in `worker/`; every subprocess call is an explicit argument array (`worker/src/core/ffmpeg.py:243`, `worker/src/core/whisper_cpp.py:246`, `worker/src/core/job.py`, `worker/src/services/*`). `FFMPEG_BIN` is restricted to an allowlist (`FFMPEG_ALLOWLIST = {"ffmpeg","ffmpeg.exe"}`, `ffmpeg.py:44`) and `validate_input_path()` rejects NUL + shell metacharacters (`ffmpeg.py:113`). `run_ffmpeg(args: list[str])` is string-typed array only.
- **Tauri CSP (`tauri.conf.json`)**: production CSP is strict — `default-src 'self'`; `script-src 'self'` (no inline/eval in prod); `connect-src ipc: http://ipc.localhost` (**no external network**); `object-src 'none'`; `base-uri 'self'`; `frame-ancestors 'none'`; `form-action 'self'`. Media/fonts limited to `asset:`/`blob:`/`data:`. Dev CSP only relaxes script/style `'unsafe-inline'` + localhost:1420.
- **Tauri capabilities (`capabilities/default.json`)**: deny-by-default; only `core:default` + `dialog:default` on the main window. **No** shell/process/fs/http broad grants.
- **Logging audit**: no logger line in Rust (`src-tauri/src/**`) or Python (`worker/src/**`) logs an API key/token/secret. The sidecar auth token appears only in the stdout READY handshake (`worker/src/main.py:74`, echoed to parent process), never in logs or argv. `save_secret` errors include no secret material.

### 3b. Credential Manager (real-vault verification) — ⚠ CRITICAL FINDING + FIX

**Finding:** `Cargo.toml` declared `keyring = "3"` **without the `windows-native` feature**. The keyring crate's compile-time default on Windows is then the **in-memory mock store** (`keyring-3.6.3/src/lib.rs:296-297`). Result: `set` returned `Ok`, `get` returned `Ok(None)`, `delete` "succeeded", and **nothing was ever written to Windows Credential Manager** — API-key saves were silently simulated in-process and lost on restart. This violated FIX #8's "no fallback" intent and meant stored API keys were never actually protected.

**Reproduction:** added `#[ignore]`d integration test `real_vault_roundtrip_windows` (`secret_store.rs`) exercising `KeyringVault` directly through Windows Credential Manager (level-1 evidence). Before fix: FAILED with `get → None`; `cmdkey /list` showed no credential. Cross-check: standalone probe with `keyring = { features=["windows-native"] }` round-trips correctly (SET_OK / GET match / DELETE / NoEntry).

**Fix:** `src-tauri/Cargo.toml:34` → `keyring = { version = "3", features = ["windows-native"] }`. `cargo build` now pulls `windows-sys 0.60.2` and compiles the real backend.

**Verified after fix (this machine, real Windows vault):**
- Mock suite: 6/6 PASS.
- Real-vault round-trip `--ignored`: **PASS** — set → get (full secret matches) → delete → get returns `None`; credential appeared in `cmdkey /list` as `LegacyGeneric:target=gemini.ai-video-localization-audit` and was removed after cleanup (no leftovers).
- FIX #8 behavior preserved: `KeyringVault` maps any non-`NoEntry` keyring error to `SecretStoreError::Unavailable` with "key was not saved" (no silent fallback).

**Impact on release:** this was a real P1 for shipped behavior (API-key persistence). Fixed + verified; the new `#[ignore]`d test is CI-safe (runs only on a real OS vault) and the mock tests keep the contract safe everywhere.

---

## Gate 4 — License decision

**Status: BLOCKED — OWNER DECISION REQUIRED.** Project license is still TBD (`README` "License" TODO, `src-tauri/Cargo.toml` `license` unset). No `LICENSE` file will be added until the owner chooses (MASTER_PLAN §21: never assert an unverified license). See `LICENSING.md`.

---

## Gate 5 — End-user documentation

**Status: pending.** The engineering doc set is complete (RELEASE-P1-002). The audit flags a missing user-facing install/use/troubleshooting guide — to be added if time permits within this session.

---

## Gate 6 — Final regression

**Status: PASS** — full suite green on the post-security-fix tree (commit `401dc16`), installers rebuilt and verified end-to-end.

| Layer | Command | Result |
|---|---|---|
| Worker | `py -3.13 -m pytest tests -q -p no:cacheprovider` | **583 passed**, 1 deselected (the `@pytest.mark.ai` live-Gemini test needs a real `GEMINI_API_KEY` — absent by design, correctly deselected) |
| Rust | `cargo fmt --check` | clean |
| Rust | `cargo check` | clean |
| Rust | `cargo clippy --all-targets --all-features -- -D warnings` | clean (0 warnings) |
| Rust | `cargo test` | **162 passed**, 0 failed |
| Frontend | `npm run typecheck` | clean |
| Frontend | `npm run lint` | clean |
| Frontend | `npm run format` | clean (all already formatted) |
| Frontend | `npm run test` (vitest) | **136 passed** (22 files) |
| Frontend | `npm run build` | clean (271 kB JS, 24 kB CSS) |
| Golden E2E (source worker) | `run_golden.py --device cpu` | **16/16 PASS in 4.3 s** |
| Golden E2E (packaged worker) | `--worker-exe <install>\worker\worker.exe` | **16/16 PASS in 4.7 s** |
| Security | `gitleaks git` (final) | 71 commits scanned, **no leaks** |
| Security | `cargo-deny check licenses advisories bans sources` | **all ok** |

Packaging after the keyring fix:
- `npx tauri build` (cargo on PATH) → fresh MSI + NSIS installers containing the `401dc16` binary.
- Fresh silent install (`/S /D=temp`) → exit 0; `ai-video-localization.exe`, `uninstall.exe`, `worker\worker.exe`, `worker\_internal\*`, `ffmpeg\{ffmpeg,ffprobe}.exe` extracted.
- Installed-worker golden E2E (see table) PASS.
- `uninstall.exe /S` → install directory removed entirely (no leftovers).

Outstanding (documented, not re-runnable in this environment):
- Live `GEMINI_API_KEY` provider test — needs a real key; network + key intended for pre-beta, not CI.
- Clean-VM first-run model download — needs a fresh OS (see Gate 1).
- NVENC encode session — needs a desktop GPU with a working NVENC session (see Gate 2).

---

## Gate 7 — Final automation deep audit (pre-release)

**Status: PASS** — full pre-release audit of the AUTOMATION pipeline on the
final tree (see `docs/FINAL_AUTOMATION_AUDIT.md` for the full report).

Fixes landed in this gate (all verified by tests):

1. **Cancel now reaches the worker mid-stage** — worker cancel registry +
   in-flight abort; Rust `run_stage` polls cancel + progress every 250 ms.
2. **Live progress during long stages** — token-scoped progress registry +
   `/v1/jobs/{id}/progress`; STT duration baseline derived from the extract.
3. **Export timeout** raised from 3 s read timeout to the 1 h pipeline IO
   timeout (large video export + QC would otherwise fail).
4. **Translate/subtitle worker-cancellable** with per-block progress.
5. **Automation render validates burn-in** (`check_window` from the cue list).
6. **CompletionView shows the real source language** (was hard-coded
   "Auto Detect").
7. **Media serving capped at 32 MiB chunks** — `Range: bytes=0-` on a multi-GB
   clip previously loaded the whole file into RAM (OOM risk).
8. **Packaged worker bundles `google-genai`** (pyproject + PyInstaller spec) —
   Gemini was unreachable in the production bundle; rebuilt and probed PASS.
9. **Provider default is now `gemini`** — the UI previously defaulted to the
   mock provider, so a production run could silently produce fake subtitles;
   mock remains an explicit opt-in and the key guard + banner covers no-key.

Final-tree evidence:

| Layer | Result |
|---|---|
| Worker | **589 passed**, 1 deselected (live Gemini — needs real key) |
| Rust | **169 passed**; fmt + clippy clean |
| Frontend | typecheck clean; **152 passed** (23 files) |
| Golden E2E (source + packaged worker) | **16/16 PASS** each |
| Packaged-worker Gemini probe | PASS |
| ffprobe output validation | PASS (h264 640×360 25 fps + aac, 6.44 s) |
| Production build (`npx tauri build`) | PASS |

Repository cleanup: removed `output/` (747 MB generated media) + stray build
logs; `.gitignore` now covers `output/` and `.agents/`.

Outstanding (unchanged, owner-side):
- Live `GEMINI_API_KEY` provider test — needs a real key.
- Clean-VM first-run model download — needs a fresh OS.
- NVENC encode session — needs a desktop GPU (libx264 fallback verified).
- The user's real ~40-minute video test — the definitive scale check.

---

## Provider Management (dynamic provider registry)

Implemented `feat: add dynamic provider management`:

- Migration v8: `providers` + `provider_defaults` tables; seeded **FREE** (default,
  immutable) + gemini/local/mock builtins.
- Rust `ProviderService` (CRUD, capability-level defaults, delete-default → FREE
  fallback, resolve_translation) + `providers.*` IPC commands.
- SecretStore now accepts dynamic provider ids (shape-validated) — keys stay in
  the OS credential vault, never in SQLite, never returned to the UI.
- Worker: `free` kind in `build_translation_provider` + `POST /v1/providers/test`
  (Save & Test stores a key only on success).
- PipelineRunner resolves the translate provider from the registry (no
  hard-coded fallback); disabled/missing providers fail explicitly.
- UI: Settings → Providers (cards, add/edit form, test, set default,
  enable/disable, delete confirm); Automation/Projects provider dropdowns and
  the Dashboard provider status are registry-driven.
- Tests: worker **601 passed** (12 new provider tests), Rust **178 passed**
  (9 new provider/migration/runner tests), frontend **156 passed**
  (4 new store tests), golden E2E **16/16**, production build **PASS**.

## Gate 8 — FINAL ACCEPTANCE (real ~48-minute video) — PASS 2026-08-12

- **Real-video acceptance**: the user's 48.6-min video (2918.3 s, 852×480,
  Chinese narration) ran the full automation — extract → real STT
  (faster-whisper turbo on CUDA, 1142 segments) → translate route (mock,
  offline) → 959-cue subtitle burn-in → render (original audio preserved) →
  export → **15/15 PASS in 14.0 min** → `D:\Downloads\New\聊斋动画_越南语字幕.mp4`
  (267 MB, ffprobe-validated, QC 0 issues).
- **P0 fixed (Bug 11)**: CUDA STT returned HTTP 500 on GPU machines
  (`cublas64_12.dll` not found). Fix: `worker/src/core/cuda_libs.py` registers
  pip-provided CUDA DLL dirs (PATH + `add_dll_directory`); `stt_service`
  retries once on CPU for CUDA runtime-library failures (no more 500);
  `resolve_device("auto")` probes CUDA via ctranslate2 without torch;
  `pyproject.toml` gains a `cuda` extra; +6 unit tests.
- **UX fixes (previous session)**: safeInvoke + dialog-picker wrappers so the
  app degrades gracefully outside the Tauri window; `scripts/tauri.cjs`
  (cargo PATH) + `scripts/dev.cmd` launchers.
- Tests: worker **607 passed** (1 deselected — live Gemini), Rust **178
  passed**, frontend **156 passed** + typecheck + lint clean, golden E2E
  **16/16**, real 48-min video **15/15**, production build **PASS**.
- Verdict: **READY FOR PERSONAL USE** (see `docs/FINAL_ACCEPTANCE_REPORT.md`).
  Remaining non-blockers: real AI translation needs a Gemini key / local LLM
  server (this run used offline mock); TTS + logo removal not in this build;
  packaged worker does not bundle CUDA libs (source worker used for GPU STT).

## Gate 9 — TTS DUBBING STAGE (local voice over) — PASS 2026-08-12

- **New stage**: Automation now supports a real dubbing stage between
  translation and render. `worker/src/services/tts_service.py` synthesizes
  each translated cue into a full-duration 44.1 kHz mono voice track
  (`/v1/tts/synthesize`), and `render_service` mixes it over the original
  audio (original ducked to 45%, format preserved — the mix adopts the
  source's channels/sample rate so render QC still passes).
- **Engines**: `edge-tts` (online, Microsoft neural voices, default) and
  `piper` (offline; vi/zh medium models auto-download once to
  `~/.cache/piper-voices`). Both verified live. Piper API compat shim for
  the new chunk-based piper (>=1.6) vs legacy rhasspy API.
- **Frontend**: Automation page enables the Voice & dubbing block (Dub audio
  checkbox + voice + engine select); the pipeline plan gains the `tts` stage
  when dubbing is on (5 stages, dynamic progress slices); render requests
  the voice track.
- **Bugs fixed during E2E**: (1) atempo fit branch renamed the cue wav
  backwards (`Path(wav).replace(fit)` — left the fitted audio at
  `*.fit.wav` and the track assembly failed); (2) same class of backwards
  rename in the piper normalizer; (3) voice mix forced stereo 44.1 kHz
  instead of the source format (render QC 422 on mono 22.05 kHz sources);
  (4) `_DEFAULT_VOICE["ja"]` pointed at a Vietnamese voice.
- Tests: worker **627 passed** (1 deselected), Rust **182 passed**, frontend
  **159 passed** + typecheck + lint clean, golden E2E **16/16**, golden dub
  E2E (TTS + mix + export) **13/13**.

## Gate 10 — AUTOMATION LIVE LOG CONSOLE — PASS 2026-08-13

- **Live log under the video workspace**: Automation now shows a live-log
  console fed entirely by real backend events — `job:status` (progress,
  stage) and `job:log` (console lines) emitted by Rust JobService via the
  Tauri event bridge, plus worker detail lines forwarded by the runner.
- **Real data, nothing fabricated**: worker `CancellationToken` gained a
  `message` field; stage `on_progress` callbacks emit real detail lines
  (STT `123s/2918s`, translate `64% translated`, TTS `segment 81/127`,
  render `63% encoded`). The Rust runner forwards each changed message to
  the live log once. ETA is computed from real progress velocity and hidden
  when there is not enough data.
- **UI**: current-task panel (stage, last message, real progress bar,
  elapsed, ETA), vertical stage timeline with real job timings, console
  with level colors (info/success/warn/error), auto-scroll with a sticky
  "↓ New logs" pill when the user scrolls up, collapse + drag-resize +
  clear + max-lines cap (200/500/1000, persisted to localStorage),
  Cancel / Retry buttons, completed summary (total time + output path +
  Open Output / Open Folder via new `system.reveal`), and a failed summary
  (stage + error code + expandable details).
- **Job history on reload**: stage-level history is backfilled from the
  persisted `jobs` table (raw per-segment lines are ephemeral by design);
  the output preview refreshes automatically when the render stage
  succeeds (no app reload needed).
- **Stale UI fixed**: removed the leftover "Voice & dubbing — Later — not
  in this build" placeholder and the Dashboard "TTS: not in this build"
  label (TTS shipped in Gate 9).
- Tests: worker **628 passed** (1 deselected), Rust **182 passed**
  (ProgressResponse message + runner log forwarding), frontend **178
  passed** (+12 log-helper +7 LiveLogView), golden E2E **16/16**, golden
  dub E2E **14/14** (new live progress-message probe: `segment 1/4` seen
  on the wire). Frontend build PASS.
