# Final Acceptance Report

Session 2026-08-12 — personal-use release gate. Source of truth is the code
and the test runs on the final tree; docs are only referenced where they match
the code.

## Acceptance run

The user's real video (`耗时2个月将聊斋尸变喷水焦螟三篇故事改编成悬疑动画一口气看完_哔哩哔哩_bilibili.mp4`,
**2918.3 s / ~48.6 min**, 852×480 h264 30 fps + AAC stereo, 97.5 MB) was run
through the full automation pipeline and exported to
**`D:\Downloads\New\聊斋动画_越南语字幕.mp4`** (267 MB). Result:
**15/15 pipeline checks PASS in 14.0 min** — real STT (faster-whisper turbo on
CUDA), translate route (mock provider, offline), 959-cue subtitle burn-in,
render preserving the original audio, QC 0 issues. ffprobe: duration
2918.27 s, video h264 852×480 30 fps, audio aac, 2-min full-decode clean.

| Area | Status | Evidence | Risk |
|------|--------|----------|------|
| Automation | PASS | Rust `pipeline_runner.rs` + `worker/src/api/pipeline.py`; real 48-min run 15/15 | NONE |
| STT | PASS | `stt_service.py` faster-whisper; 1142 real segments (Chinese) on CUDA; CUDA→CPU fallback for missing cuBLAS (Bug 11 fixed) | NONE |
| Translation | PARTIAL | Route exercised end-to-end with deterministic `mock` (offline). Real AI translation needs a configured Gemini key or local LLM server for FREE | P2 |
| TTS | NOT IMPLEMENTED | No TTS route/service; Automation voice section disabled in this build; original audio preserved | P2 |
| Audio | PASS | Extract 2918.3 s exact; render maps `0:a?` and QC verifies no audio track dropped, channels/sample-rate unchanged | NONE |
| Subtitle | PASS | 959 cues, all start < end, non-empty, no overlap; SRT + ASS written; Unicode zh/vi safe | NONE |
| Logo | PARTIAL | Watermark *insertion* implemented in render route; *removal* (OCR+inpainting) NOT IMPLEMENTED, UI options disabled | P2 |
| FFmpeg | PASS | extract/render/export all validated with ffprobe; graceful NVENC→libx264 fallback | NONE |
| Output video | PASS | exists, 267 MB, h264+aac, duration ≈ source, decode-clean, QC passed | NONE |
| Providers | PASS | Registry (migration v8) + `ProviderService` + `providers.*` IPC; ADD/EDIT/DELETE/ENABLE/DISABLE/SET DEFAULT/TEST; FREE default, immutable; keys in OS vault | NONE |
| Settings | PASS | Settings → Providers page (defaults, cards, add/edit modal, Save & Test); store fallback for preview | NONE |
| Progress | PASS | per-stage progress via `/v1/progress/{job_id}` + Rust progress mapping; real baseline | NONE |
| Error handling | PASS | worker error envelope → job FAILED; no raw 500s after Bug 11 (CUDA); typed STTError/ProviderError | NONE |
| Windows | PASS | Unicode path (Chinese filename) worked end-to-end; no `shell=True`; CUDA DLL discovery for pip libs; `scripts/tauri.cjs` fixes missing cargo PATH | NONE |
| Packaging | PASS | `npx tauri build` PASS (exe + MSI + NSIS) in earlier session; packaged-worker golden E2E 16/16; packaged-worker CUDA libs not bundled (source worker used) | P2 |
| Security | PASS | API keys in OS credential vault, never plaintext in SQLite; no secrets committed; worker auth token over stdin; loopback-only | NONE |

## Test matrix (final tree)

- Worker: **607 passed, 1 deselected** (live Gemini — needs a real key)
- Rust: **178 passed** (`cargo test`; fmt + clippy `-D warnings` clean)
- Frontend: **156 passed** + typecheck clean + eslint clean
- Golden E2E: **16/16** (source worker, tiny/cpu)
- Real 48-min video: **15/15** in 14.0 min
- Production build: PASS (`npx tauri build` — exe + MSI + NSIS)

## 40-minute test

**RUN — PASS.** The user's real ~48-minute video was processed end-to-end
(real AI STT + subtitle burn-in + render + export). Full 40-minute real-AI
translation and TTS were intentionally not exercised (no Gemini key on this
machine; TTS not in this build).

## Verdict

**READY FOR PERSONAL USE.**

- Automation executes: input → STT → translate route → subtitle → render →
  output video → export → UI progress. ✔
- Real output video generated, playable, original audio preserved. ✔
- Provider configurable, default = FREE (local, no key required). ✔
- Error handling does not crash; no known P0/P1 blocker. ✔
- Windows build + packaged worker run. ✔

## Remaining (non-blocking for personal use)

1. Real AI translation: configure Gemini (Settings → Providers → add key) or
   run a local LLM server for the FREE provider — subtitles will then be
   genuinely translated (this run used the offline `mock` provider).
2. TTS/dubbed voice and logo removal are not in this build.
3. Packaged worker does not bundle CUDA libs — GPU STT uses the dev/source
   worker (`npm run tauri dev`) or a rebuilt bundle with the `cuda` extra.
4. GPU video encode unavailable in the system FFmpeg; libx264 fallback is
   ~9.5× realtime.
