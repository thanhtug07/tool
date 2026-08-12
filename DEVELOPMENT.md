# DEVELOPMENT.md

Development guide for the **AI Video Localization Studio** desktop app (Tauri 2 + React + Rust core + Python AI worker). This document is part of the release documentation set required by `MASTER_PLAN.md §44`.

## Repository layout

```text
├── src/                      # React + TypeScript + Vite frontend (renderer)
│   ├── api/                  # typed bridges over the Tauri IPC surface
│   ├── components/           # shared UI components + layout
│   └── pages/                # Projects / Dictionary / Subtitles / Preview / Settings / About
├── src-tauri/                # Rust core (Tauri 2)
│   ├── src/
│   │   ├── commands/         # IPC command handlers (project, job, subtitle, dictionary, settings, export, pipeline)
│   │   ├── services/         # business logic: job_service, worker_client, worker_manager, settings_service, cache_service
│   │   ├── db/               # SQLite (rusqlite): versioned migrations + repos
│   │   └── security/         # secret_store (Windows Credential Manager via keyring)
│   ├── capabilities/default.json   # Tauri capability scoping (deny-by-default)
│   └── tauri.conf.json       # app config, CSP, bundle settings
├── worker/                   # Python AI worker (FastAPI on 127.0.0.1, bearer-token IPC)
│   ├── src/api/              # pipeline.py + routes.py (HTTP surface), schemas.py (shared JSON models)
│   ├── src/services/         # media, audio, stt, translation, quality, subtitle, render, export, cache, hardware, models
│   ├── src/core/             # ffmpeg wrapper, job primitives, token auth
│   ├── scripts/              # benchmark_performance.py, golden_video_test.py
│   ├── packaging/            # PyInstaller onedir worker bundle (build_worker.py, worker.spec)
│   └── tests/                # unit/ + integration/ + fixtures/
├── schemas/                  # shared JSON schemas (single source of truth)
├── golden/                   # golden video + translation benchmark fixtures & runners
├── scripts/                  # repo automation (autonomous_runner, vendor_ffmpeg, ...)
├── docs/                     # autonomous-orchestrator state + release audit
└── vendor/                   # (gitignored) bundled ffmpeg / models
```

## Prerequisites

- Node.js ≥ 22 (npm)
- Rust toolchain (stable, 2024/2025 edition-compatible)
- Python 3.11+ (the worker pins 3.11; see `ARCHITECTURE_DECISION.md`)
- FFmpeg + FFprobe on `PATH` for dev (release builds use the vendored bundle)
- WebView2 runtime on Windows

## Frontend

```powershell
npm install
npm run dev          # Vite dev server
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm run format       # prettier (write)
npm run format:check # prettier (check only)
npm run test         # vitest
npm run build        # vite build
```

## Rust core

```powershell
cd src-tauri
cargo check
cargo test
cargo clippy -- -D warnings
cargo fmt --check
```

The IPC command surface is registered in `src-tauri/src/lib.rs` via `tauri::generate_handler![...]`; each command carries a `rename = "namespace.method"` (see `src-tauri/src/commands/*.rs`).

## Worker

```powershell
cd worker
pip install -e .      # installs deps declared in pyproject.toml
py -m src.main --port 43117   # standalone dev server (token handshake via stdin)
py -m pytest tests -q -p no:cacheprovider   # full suite (the `ai` marker is excluded by default)
py -m pytest tests/unit/test_translation_service.py -q   # subset
```

The worker binds loopback only and requires a per-session bearer token (stdin handshake). See `API.md` for the route list.

## Tests

- `npm run test` (frontend, vitest)
- `cargo test` (Rust)
- `py -m pytest tests` (worker; `-m "not ai"` default via `pyproject.toml`)
- Integration tests under `worker/tests/integration/` invoke **real ffmpeg/ffprobe** (skipped when not on `PATH`); fixtures are small deterministic synthetic clips.
- AI-marker tests (real STT inference / real Gemini call) are excluded by default because they need models or API keys.

## Packaging (release)

```powershell
python worker/packaging/build_worker.py   # PyInstaller onedir -> worker-dist/worker/
python scripts/vendor_ffmpeg.py           # fetch FFmpeg/FFprobe into vendor/ffmpeg/
npx tauri build                           # MSI + NSIS installers under target/release/bundle/
```

Release-mode `WorkerManager` spawns the bundled `worker.exe` (from the Tauri resource dir); dev builds spawn `python -m src.main`. See `RELEASE.md`.

## Benchmarks

```powershell
cd worker
py scripts/benchmark_performance.py --minutes 1 10 30 60 --model tiny --device cpu
```

Writes `worker/perf_report.json` (see `RELEASE_READINESS_AUDIT.md` / `docs/AUTONOMOUS_PROGRESS.md` for the latest results).

## Contribution notes

- Follow the rules in `AGENTS.md` (one task = one gate; never change architecture/scope without approval; never commit secrets).
- Docs are written in Vietnamese by default; keep new docs consistent with the existing set.
- Before reporting a task complete: run typecheck + lint + format + the relevant layer tests.
