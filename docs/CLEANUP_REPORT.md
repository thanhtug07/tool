# Cleanup Report

Session 2026-08-12 — final pre-release repository hygiene. Rule applied
before every deletion: search the repo for references, verify imports/build/
tests/docs usage, then delete only what is provably unused.

## Deleted

| Item | Reason |
|------|--------|
| `output/` (747 MB generated test media) | Generated artifacts from earlier runs; `.gitignore` covers `output/` (added in the prior release session) |
| `cf446a.log`, `worker-dist-build.log` | Stray build/test logs, ignored |
| `D:\Downloads\New\_pipeline_work/` (staging: wav, json, rendered.mp4, srt, ass) | Pipeline staging for the acceptance run; the final exported video lives at `D:\Downloads\New\聊斋动画_越南语字幕.mp4` |
| `D:\Downloads\New\_repro_stt.py`, `_repro_path.py`, `_repro.log/.err`, `_real_run.log/.err`, `_clip.wav` | Temporary debug/run files outside the repo used during the CUDA diagnosis |

## Retained (verified still referenced)

| Item | Reason |
|------|--------|
| `scripts/demo`, `scripts/models`, `scripts/vendor_ffmpeg.py`, `scripts/generate_*.py` | Referenced by tracked docs / used by build tooling |
| `golden/` (video/audio/expected/results, `run_golden.py`, `generate_golden.py`, `probe_gemini_bundle.py`) | Golden E2E fixtures + runners required by release gates |
| `.agents/` | Freebuff tooling directory (gitignored, not project content) |
| `docs/*` | Authoritative documentation (MASTER_PLAN/AGENTS-referenced) |
| `vendor/ffmpeg` | Bundled FFmpeg used by the packaged worker |
| `worker-dist/` | Rebuilt worker bundle (gitignored) |

## Added (this session)

- `golden/scripts/run_real_video.py` — acceptance runner for a real long video.
- `worker/src/core/cuda_libs.py` — CUDA DLL discovery (P0 fix).
- `src/api/invoke.ts`, `src/api/dialog.ts`, `scripts/tauri.cjs`, `scripts/dev.cmd`
  — IPC safety + dev launcher (previous session's fixes, now committed).

## Repository state after cleanup

- `git status`: only intentional source/doc changes (see commit list).
- No secrets, API keys, model binaries, generated media, or caches tracked.
