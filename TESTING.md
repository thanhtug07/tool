# TESTING.md

Testing strategy and commands for **AI Video Localization Studio**. Required doc per `MASTER_PLAN.md §22/§44`; maps to `MASTER_PLAN §29` and `AGENTS.md`.

## Layers

| Layer | Tool | Where | Default command |
|---|---|---|---|
| Frontend unit | vitest | `src/**/*.test.tsx` / `*.test.ts` | `npm run test` |
| Frontend type/lint/format/build | tsc / eslint / prettier / vite | `src/` | `npm run typecheck`, `npm run lint`, `npm run format:check`, `npm run build` |
| Rust unit | cargo test | `src-tauri/src/**` (inline `#[cfg(test)]`) | `cargo test` (+ `cargo clippy -- -D warnings`, `cargo fmt --check`) |
| Worker unit | pytest | `worker/tests/unit/` | `py -m pytest tests/unit -q` |
| Worker integration (real ffmpeg) | pytest | `worker/tests/integration/` | `py -m pytest tests/integration -q` (skips if ffmpeg absent) |
| Worker AI (real inference) | pytest `ai` marker | `worker/tests/**` | `py -m pytest tests -m ai` (excluded by default — needs model/key) |
| Secret scan | gitleaks | repo | `gitleaks detect` (CI) |
| License audit | cargo-deny / pip-licenses | repo | CI job (`licenses`) |

## Worker suite details

- `pyproject.toml`: `addopts = "-m 'not ai'"` → the default run excludes slow/AI tests; the `ai` marker is for real STT / real provider runs.
- Integration tests run **real ffmpeg/ffprobe** on small deterministic synthetic fixtures under `worker/tests/fixtures/media/` (MP4/MKV/MOV/WebM, tiny h264 clips, with/without audio/subtitles). They `skipif` when ffmpeg is not on `PATH`.
- Coverage highlights (by service):
  - media probe: unit + integration (corrupt files, rotation, containers)
  - audio extract: integration (real ffmpeg, no-audio, cancel, injection)
  - render: integration (real ffmpeg burn-in, HW/CPU encoders, fallback, cancel, watermark region checks, export + QC)
  - translation: unit (TM hit/miss, glossary invalidation, dedup, segment-id regression, quality gate)
  - subtitle: unit + integration (ffmpeg parse of generated ASS/SRT)
  - export: unit (path resolution, QC report, SRT↔VTT) + integration (ffprobe verify)

## Release-quality runners

- **Golden video E2E:** `py golden/scripts/run_golden.py` — drives the full pipeline over a deterministic synthetic (piper-speech) clip and checks the 12 `GOLDEN_VIDEO_TEST.md` checkpoints (16/16 PASS recorded in dev and packaged modes).
- **Translation benchmark:** golden translation manifest + runner (`golden/translation/manifest.json`).
- **Performance benchmark:** `py worker/scripts/benchmark_performance.py --minutes 1 10 30 60` → `worker/perf_report.json` (1/10/30/60-min results all PASS on CPU; GPU NOT VERIFIED — missing CUDA libs; see `docs/AUTONOMOUS_PROGRESS.md`).

## CI (`.github/workflows/ci.yml`)

Jobs cover frontend (typecheck/lint/test), Rust (fmt/check/clippy/test), worker pytest (no `ai`), a `licenses` job (cargo-deny), and a `security` job (gitleaks). CI uses lockfiles (`package-lock.json`, `Cargo.lock`).

## Run order before reporting work complete

Per `AGENTS.md`: `npm run typecheck` + `npm run lint` + `npm run format` + relevant layer tests (`npm run test`, `cargo test`, `py -m pytest tests -q -p no:cacheprovider`). Latest full-suite results are recorded in `docs/AUTONOMOUS_PROGRESS.md`.

## Known gaps (see `RELEASE_READINESS_AUDIT.md`)

- No E2E test framework wired into CI (golden runner is manual).
- 1/10/30/60-min performance benchmarks exist; GPU runs are blocked by missing CUDA toolkit libs locally.
- Real AI inference (`ai` marker) and real Gemini calls need models/keys — currently exercised ad hoc, not in CI.
