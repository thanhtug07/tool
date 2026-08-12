# E2E AUTOMATION STATUS

Trace of the real automation path: **Frontend → Tauri IPC → Rust → JobService → Worker → stages → FFmpeg → final video**. Statuses: `PASS` / `PARTIAL` / `BLOCKED` / `MOCK` / `MISSING`.

## The full chain

| # | Arrow / stage | Implemented | Connected | Actually called | Mock? | Returns real data? | Handles errors? | Can process a real video? | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Automation UI ⚡ AUTOMATE | Yes (`src/pages/Automation`) | Yes | Yes (button) | No | — | Yes (validation, toasts) | Yes | **PASS** |
| 2 | Frontend → IPC `project.create` | Yes (`src/api/project.ts`) | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 3 | IPC → Rust `commands/project.rs` → `ProjectService` | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 4 | Frontend → IPC `job.submit` ×4 | Yes (`src/api/job.ts`) | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 5 | Rust `JobService` → queue → `PipelineRunner` | Yes | Yes | Yes | No | Yes | Yes (retry taxonomy, cancel) | Yes | **PASS** |
| 6 | Rust → Worker HTTP (`/v1/audio/extract`) | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 7 | FFmpeg audio extract | Yes (`audio_service.py`, bundled ffmpeg) | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 8 | Rust → Worker (`/v1/stt/transcribe`) | Yes | Yes | Yes | No | Yes (real faster-whisper) | Yes | Yes (model required) | **PASS*** |
| 9 | STT → transcript artifact | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 10 | Rust → Worker (`/v1/translate`) | Yes | Yes | Yes | `mock` default | Mock = deterministic pseudo-translation; Gemini/local = real but unproven live | Yes | Yes (mock) / BLOCKED without key (gemini) | **PARTIAL** |
| 11 | Translation → artifact + TM | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 12 | Rust → Worker (`/v1/subtitle`) | Yes | Yes | Yes | No | Yes (rule-based, no model) | Yes | Yes | **PASS** |
| 13 | Subtitle ASS/SRT + editor sync | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 14 | Rust → Worker (`/v1/render`) | Yes | Yes | Yes | No | Yes (real ffmpeg) | Yes (QC, cancel) | Yes | **PASS** |
| 15 | Burn-in + watermark → final video | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** |
| 16 | Realtime stage/progress/elapsed + cancel (UI) | Yes | Yes | Yes | No | Yes (real stage/progress from jobs; ETA honestly omitted — the backend reports stages, not ETA) | Yes | Yes | **PASS** |
| 17 | Completion → Play / Export (QC) / Copy path / Re-run / Edit subtitles | Yes | Yes | Yes | No | Yes | Yes | Yes | **PASS** — no "Open folder": no reveal-in-Explorer command exists in the backend, so the UI omits it instead of faking it |
| 18 | **TTS / dubbing (generate target speech)** | **No** | **No** | **No** | — | — | — | **No** | **MISSING** |
| 19 | **Audio mixing / background-music preservation** | **No** | **No** | **No** | — | — | — | **No** | **MISSING** |
| 20 | **Dubbed-speech timing/alignment** | **No** | **No** | **No** | — | — | — | **No** | **MISSING** |
| 21 | **Logo/watermark removal** | **No** | **No** | **No** | — | — | — | **No** | **MISSING** |

\* `PASS` for STT requires a faster-whisper model to be present (auto-downloaded on first use; only `tiny` cached on this machine — a `large-v3` run triggers a ~3 GB download).

## Verdict per product promise

| Promise | Status |
|---|---|
| Analyze video (probe) | **PASS** |
| Extract audio | **PASS** |
| Separate speech/background | **MISSING** |
| Transcribe speech | **PASS** (model required) |
| Translate transcript | **PARTIAL** (mock proven; Gemini key needed for real translation) |
| Generate target-language speech | **MISSING** |
| Preserve background/music | **MISSING** |
| Synchronize dubbed speech | **MISSING** |
| Generate subtitles | **PASS** |
| Burn/insert subtitles | **PASS** |
| Remove source logo/watermark | **MISSING** (adding a watermark is PASS) |
| Export final video | **PASS** (translated + subtitled, NOT dubbed) |

## Conclusion

Every arrow that exists is genuinely wired and exercised — there is no dead UI or stub stage in the implemented set. The automation currently produces a **translated + subtitled + watermarked video**. The **dubbed** output is impossible today: TTS, audio mixing and timing/alignment (rows 18–20) are entirely absent, plus logo removal (row 21). These are the `MISSING` gaps that define the demo blocker.
