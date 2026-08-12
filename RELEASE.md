# RELEASE.md

Release process and status for **AI Video Localization Studio** (Tauri 2 desktop app). Required doc per `MASTER_PLAN.md §22/§44`; maps to `MASTER_PLAN` Phase 13/14, `TASKS.md §6`, and the release audit in `docs/RELEASE_READINESS_AUDIT.md`.

## Build & packaging

```powershell
# 1. bundled worker (PyInstaller onedir)
python worker/packaging/build_worker.py          # -> worker-dist/worker/worker.exe + _internal/

# 2. vendored FFmpeg/FFprobe (release builds resolve via FFMPEG_BIN/FFPROBE_BIN)
python scripts/vendor_ffmpeg.py                  # -> vendor/ffmpeg/

# 3. Tauri release build
npx tauri build                                  # -> target/release/bundle/msi, nsis
```

Artifacts: release `.exe`, `.msi`, and `.exe` NSIS setup, all bundling the worker runtime and FFmpeg (verified: MSI 184.7 MB / NSIS 133.4 MB contain `worker.exe`, `_internal`, `ffmpeg.exe`, `ffprobe.exe` — RELEASE-P0-008, rebuilt on the fixed tree Gate 6).

## Release-mode worker

- `WorkerManager` differs by build: **release** spawns the bundled `worker.exe` from the Tauri resource dir; **dev** spawns `python -m src.main` (`src-tauri/src/services/worker_manager.rs`).
- Lifecycle: read auth token per-session over stdin → `READY <token>` handshake → authenticated `/health` → job dispatch over the loopback HTTP API. Portable self-contained run PASSES from outside the repo (RELEASE-P0-008).

## Release gates (status from `docs/RELEASE_READINESS_AUDIT.md` + `docs/RELEASE_PROGRESS.md` + `docs/AUTONOMOUS_PROGRESS.md`)

| Gate                                           | Status             | Evidence                                                                                  |
| ---------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------- |
| All TASKS.md tasks (001–030)                   | ✅ PASS            | committed; worker/rust/frontend suites green                                              |
| Layer gates (typecheck/lint/format/test/build) | ✅ PASS            | 583 worker / 162 rust / 136 frontend                                                      |
| Golden video E2E                               | ✅ PASS            | 16/16 dev + packaged + installed-worker (RELEASE-P0-006/007, Gate 6)                     |
| Performance benchmarks 1/10/30/60 min (CPU)    | ✅ PASS            | `worker/perf_report.json` (RELEASE-P1-001)                                                |
| NVIDIA GPU (real hardware)                     | ⚠️ PARTIAL         | real CUDA STT PASS (0.49s, E2E `--device cuda` 16/16); NVENC fails on this embedded GPU → libx264 fallback verified (Gate 2) |
| Security scan (gitleaks)                       | ✅ PASS            | gitleaks 8.24.3 — 71 commits, no leaks (Gate 3)                                           |
| License audit (cargo-deny/pip-licenses)        | ✅ PASS            | cargo-deny licenses/advisories/bans/sources ok; bundled-worker pip-licenses commercial-safe (Gate 3) |
| Credential Manager live round-trip             | ✅ PASS            | keyring `windows-native` fix (401dc16) + real Windows-Vault round-trip test PASS (Gate 3) |
| Installer smoke test on dev machine            | ✅ PASS            | silent install → launch → installed-worker E2E 16/16 → uninstall clean (Gate 1/6)         |
| Installer smoke test on clean machine          | ⛔ BLOCKED         | needs a clean Windows VM; not runnable locally                                            |
| Code signing                                   | ⛔ BLOCKED         | no OV certificate; unsigned installer → SmartScreen                                       |
| Auto-update                                    | ⛔ BLOCKED         | post-MVP (T038); no plugin/endpoint                                                       |

## Beta readiness

**NOT BETA READY** (see `docs/RELEASE_READINESS_AUDIT.md`): every engineering-side gate now passes at commit `401dc16` — MVP vertical slice executes end-to-end (import → STT → translate → subtitle → render → export), security + license audits PASS (incl. a **critical fix where API keys were silently stored in an in-memory mock store and never persisted to Windows Credential Manager**), real NVIDIA CUDA STT validated, installers rebuilt on the fixed tree and silently installed/launched/uninstalled on the dev machine. Beta distribution still requires owner/external actions: clean-machine installer validation, code signing (or a decision to ship unsigned), the project **LICENSE** decision, NVENC validation on a desktop GPU, and a real Gemini key.

## Uninstall

NSIS/MSI generate a default uninstaller. Dev-machine uninstall verified (`uninstall.exe /S` removed the install directory entirely — Gate 6); clean-machine uninstall remains to be exercised on the clean VM (BLOCKED on VM availability).

## Version metadata

`tauri.conf.json`: `productName` "AI Video Localization Studio", version `0.1.0`, identifier `com.tooltranslatechina.studio`. Icons set present; build succeeds.
