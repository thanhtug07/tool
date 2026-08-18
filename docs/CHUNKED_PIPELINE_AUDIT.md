# Chunked Pipeline Audit (PHASE 0)

> Deliverable of `TASK_AUTOMATION_PINELINE.md` PHASE 0. Written from the actual
> source tree (`git log -1` + code inspection). No code changed in this phase.

## Current Automation Entry

- **Frontend** — `src/workspace/StudioWorkspace.tsx`: both the Automation bar and
  the Custom tool workspace call `ctx.actions.automate()` → `handleAutomate()`.
  It validates project/worker/provider, builds a stage list
  (`automationPipelineSteps(dubAudio, logoRemoval)` in
  `src/pages/Automation/automation.ts`, or `stagesForTools(...)` for Custom),
  then `startPipelineWithSteps(...)` creates a `PipelinePlan` and
  `submitStage(first)` submits the first stage.
- **Stage chain** — the plan is a sequence of `StageKey`
  (`transcribe → translate → subtitle → [tts] → render`, logo/audio optional).
  Each next stage is submitted only after its predecessor succeeds
  (effect watching `deriveStages(plan, jobs)` in `StudioWorkspace.tsx`).
- **IPC** — `submitJob(project_id, stage, params)` → Rust command `job.submit`
  → `JobService`.

## Current Job System

- **Rust orchestrator** — `src-tauri/src/services/job_service.rs`
  (`JobService`): FIFO queue, single worker → exactly **one job runs at a
  time**. State machine guarded by `JobStatus::can_transition`. Persists every
  transition in the SQLite `jobs` table.
- **Progress** — 0..1 + sub-stage label, persisted on every update, emitted as
  `job:status` events; informational lines emit `job:log`.
- **Retry layers**:
  - whole-job auto-retry: transient failures with backoff 1s/5s/30s, max 3
    (`DEFAULT_MAX_RETRIES`);
  - stage-level retry in `pipeline_runner.rs` (`STAGE_MAX_ATTEMPTS = 3`,
    exponential backoff 2s→4s) for worker calls classified non-recoverable by
    the envelope but transient in practice;
  - worker in-call retries (edge-tts 3×1.5s, Gemini 429/5xx backoff).
- **Resume** — on startup `queued` rows re-seed the queue, rows stuck in
  `running` return to `queued` unless a cancel was requested.
- **DB** — `{data}/projects/{project_id}`; `app.db` `jobs` table columns:
  `id, project_id, type, status, progress, stage, error_code, error_message,
  error_log, params_json, retry_count, cancel_requested, created_at,
  updated_at, started_at, finished_at`.

## Current Worker System

- **Python FastAPI sidecar** — `worker/src/main.py`, spawned by Rust
  `WorkerManager` with a session token on stdin (sidecar protocol), loopback
  only (`127.0.0.1`), random port.
- **API surface** (`worker/src/api/pipeline.py` + `routes.py`):
  `/health`, `/v1/audio/extract`, `/v1/stt/transcribe`, `/v1/translate`,
  `/v1/subtitle`, `/v1/tts`, `/v1/tts/voices`, `/v1/tts/preview`,
  `/v1/render`, `/v1/logo`, `/v1/audio/process`, `/v1/export`,
  `/v1/progress/{job_id}`, `/v1/cancel`, model catalog/download.
- **Auth** — bearer token shared through the Rust client; frontend never talks
  to the worker directly.

## Current Stage System

Rust `PipelineRunner` (`src-tauri/src/services/pipeline_runner.rs`) dispatches
`JobType` → worker call (each stage runs through `run_stage`/`run_stage_retryable`
with cancellation + progress polling every 500 ms):

| JobType | Worker call | Artifact |
|---|---|---|
| `transcribe` | `/v1/audio/extract` + `/v1/stt/transcribe` | `cache/audio.wav`, `cache/transcript.json` |
| `translate` | `/v1/translate` (provider from ProviderService) | `cache/translation.json` |
| `subtitle` | `/v1/subtitle` | `cache/subtitle.srt`, `cache/subtitle.ass` + cues into `SubtitleService` |
| `tts` | `/v1/tts` (edge / piper) | `cache/voice_track.wav` |
| `render` | `/v1/render` (burn subtitle + watermark + voice/audio track) | `output/rendered.mp4` |
| `logo` | `/v1/logo` (ffmpeg delogo) | `cache/logo_removed.mp4` |
| `audio` | `/v1/audio/process` (vocal removal / normalize) | `cache/audio_mix.wav` |

All artifacts live under the validated project dir:
`{data}/projects/{project_id}/cache/*` + `{data}/projects/{project_id}/output/*`
(`ArtifactPaths` in `pipeline_runner.rs`; `DEFAULT_RENDER_NAME = "rendered"`).

## Current Temp Directory

- Per-operation temp **workdirs** are created by the worker services with
  `tempfile` (e.g. `render_service.py` copies subtitle/watermark assets into a
  generated workdir so the ffmpeg filter graph never escapes paths). Each
  service cleans its own workdir in a `finally` block.
- Rust uses `std::env::temp_dir()` only for test fixtures.
- **No centralized chunk/temp directory exists** — this is the gap PHASE 1+ fills
  (`temp/chunks`, `temp/audio`, `temp/tts`, `temp/subtitles`, `temp/intermediate`).

## Current Output Directory

- `{data}/projects/{project_id}/output/rendered.mp4` (static name, configurable
  per request). The frontend probes it (`probeMedia`) and shows `output ready`
  once it exists. Export (`/v1/export`) copies it to a user folder with ffprobe QC.

## Current Progress/Event System

- Worker reports job progress through `on_progress` callbacks; Rust polls
  `/v1/progress/{job_id}` every 500 ms (`PROGRESS_POLL_INTERVAL`) while a stage
  call is in flight, persists + emits on ≥1% delta.
- `JobService` emits `job:status` / `job:log`; frontend `src/stores/jobs.tsx`
  (`useJobs`) listens for instant updates and polls `job.list_all`.
- `LiveLog` (`src/pages/Automation/LiveLog.tsx`) renders the event stream;
  overall % is computed from stage statuses (`pipelineProgress(stages)`).
- **No per-chunk events exist** — PHASE 18 adds `CHUNK_CREATED`/`CHUNK_STARTED`/
  `CHUNK_VALID`/… event vocabulary.

## Current FFmpeg Flow

- **Audio extraction** (`audio_service.py`): ffmpeg → `pcm_s16le`, 16 kHz mono
  wav; progress mapped via `total_duration_seconds`.
- **Render** (`render_service.py`): filter graph for subtitle burn-in (ASS) +
  text/image watermark + voice/audio track mix; hardware encoder detection
  (`hardware_probe.rs` → nvidia-smi / WMI / ffmpeg encoders) with libx264
  fallback; **burn-in validation** (`_burn_in_detected`) compares source vs
  output luminance delta in the subtitle region (`E_RENDER_VALIDATION` on
  failure); `os.replace(temp_out → output)` only after validation passes.
- **No chunking / segment-based processing exists** — full-file processing only.

## Current Cleanup Flow

- Per-service temp workdirs are removed in `finally` blocks (including on
  failure — **this violates PHASE 15's "keep temp files on FAIL" rule**; the
  chunked pipeline must keep intermediates for debug/retry until output is
  verified).
- Project `cache/*` persists across runs (never cleaned); `output/*` persists.
- **No CleanupManager / state machine exists** (`PROCESSING → ASSEMBLING →
  VALIDATING → … → CLEANUP`).

## Current Provider Manager

- **Rust** — `provider_service.rs` (`ProviderService`) owns provider registry,
  enable/disable, default, needs-key state; credentials live in the OS vault
  (never logged).
- **Worker** — `translation_service.py` + `build_translation_provider(...)` in
  `api/pipeline.py` resolves `free` / `gemini` / `openai` / `local` / `mock`.
- **TTS** — `tts_service.py`: `edge` (Microsoft Neural, cloud/free) and `piper`
  (local); voice registry + preview (PHASE of Voice Library already landed).

## Current Database / Job Persistence

- SQLite `app.db` (WAL), migrations in `src-tauri/src/db/migrations.rs`.
- `jobs` (above), `projects`, `subtitles` (cue rows), settings, providers,
  glossary/characters.

## Summary of Gaps the Chunked Pipeline Must Fill

1. No chunk abstraction (`ChunkManager`, 30 s default, overlap).
2. No bounded parallel scheduler (`ChunkScheduler`).
3. No per-chunk validation/retry; no manifest; no order validation.
4. No ordered assembly (concatenate audio / merge subtitles by original time).
5. No dedicated final validation pass beyond render burn-in check.
6. Cleanup is eager per-service — must become state-machine driven, keep
   artifacts on failure, clean only after output verified.
7. Progress events are stage-level only — need chunk-level events.
8. Frontend shows stage progress only — needs chunk counts/current chunk.
