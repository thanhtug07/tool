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

Artifacts: release `.exe`, `.msi`, and `.exe` NSIS setup, all bundling the worker runtime and FFmpeg (verified: MSI 193 MB / NSIS 139 MB contain `worker.exe`, `_internal`, `ffmpeg.exe`, `ffprobe.exe` — RELEASE-P0-008).

## Release-mode worker

- `WorkerManager` differs by build: **release** spawns the bundled `worker.exe` from the Tauri resource dir; **dev** spawns `python -m src.main` (`src-tauri/src/services/worker_manager.rs`).
- Lifecycle: read auth token per-session over stdin → `READY <token>` handshake → authenticated `/health` → job dispatch over the loopback HTTP API. Portable self-contained run PASSES from outside the repo (RELEASE-P0-008).

## Release gates (status from `docs/RELEASE_READINESS_AUDIT.md` + `docs/AUTONOMOUS_PROGRESS.md`)

| Gate | Status | Evidence |
|---|---|---|
| All TASKS.md tasks (001–030) | ✅ PASS | committed; worker/rust/frontend suites green |
| Layer gates (typecheck/lint/format/test/build) | ✅ PASS | 583 worker / 160 rust / 121 frontend |
| Golden video E2E | ✅ PASS | 16/16 dev + packaged (RELEASE-P0-006/007) |
| Performance benchmarks 1/10/30/60 min (CPU) | ✅ PASS | `worker/perf_report.json` (RELEASE-P1-001) |
| Performance (GPU) | ⛔ NOT VERIFIED | CUDA toolkit libs missing (`cublas64_12.dll`); ctranslate2 sees 1 device but encode fails |
| Security scan (gitleaks) | ⛔ NOT RUN locally | gitleaks not installed; CI job exists |
| License audit (cargo-deny/pip-licenses) | ⛔ NOT RUN | tools unavailable; LICENSING checklist open |
| Installer smoke test on clean machine | ⛔ BLOCKED | needs a clean Windows VM; not runnable locally |
| Code signing | ⛔ BLOCKED | no OV certificate; unsigned installer → SmartScreen |
| Auto-update | ⛔ BLOCKED | post-MVP (T038); no plugin/endpoint |

## Beta readiness

**NOT BETA READY** (see `docs/RELEASE_READINESS_AUDIT.md`): the MVP vertical slice now executes end-to-end on the dev machine (import → STT → translate → subtitle → render → export), but the release still requires: clean-machine installer validation, code signing, GPU validation, security + license audits, and the remaining documentation deliverables (LICENSE file).

## Uninstall

NSIS/MSI generate a default uninstaller; clean-machine uninstall has not been exercised (BLOCKED on VM availability).

## Version metadata

`tauri.conf.json`: `productName` "AI Video Localization Studio", version `0.1.0`, identifier `com.tooltranslatechina.studio`. Icons set present; build succeeds.