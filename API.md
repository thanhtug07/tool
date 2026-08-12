# API.md

The application's two API surfaces: the **Tauri IPC** command set (renderer ↔ Rust core) and the **worker HTTP API** (Rust core ↔ Python worker on loopback). Required doc per `MASTER_PLAN.md §22/§44`. Shared JSON schemas live in `schemas/*.json` (single source of truth).

## Transport

| Surface | Transport | Auth | Bind |
|---|---|---|---|
| Frontend → Rust | Tauri IPC `invoke("namespace.method", ...)` | Tauri capability scoping (`capabilities/default.json`, deny-by-default, `core:default`) | in-app |
| Rust → Worker | HTTP/JSON | per-session bearer token (stdin handshake), loopback | `127.0.0.1` only |

## Worker HTTP API

Base: `http://127.0.0.1:<port>`; every route except the handshake requires `Authorization: Bearer <token>`.

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | health + auth check (`HealthResponse`) |
| `/v1/stt/transcribe` | POST | STT: WAV → transcript segments (model, device, language, job_id) |
| `/v1/audio/extract` | POST | extract 16 kHz mono WAV from a video |
| `/v1/translate` | POST | contextual translation (provider, target_language, transcript, glossary/rules) |
| `/v1/subtitle` | POST | transcript + translation → cues + ASS/SRT (+ output dir) |
| `/v1/render` | POST | burn subtitles (+ optional watermark) into a video, validated |
| `/v1/export/video` | POST | copy a rendered video to a user dir + ffprobe QC |
| `/v1/export/subtitles` | POST | copy a subtitle file, optionally SRT↔VTT |
| `/v1/jobs/{job_id}/cancel` | POST | cancel a running stage (registry-backed) |

Request/response models are defined in `worker/src/api/pipeline.py` and `schemas.py`; error payloads follow `{ "error": { "code", "message", "recoverable" } }` with codes from `MASTER_PLAN §28.1`.

## Tauri IPC commands

Thin wrappers registered via `#[tauri::command(rename = "...")]` in `src-tauri/src/lib.rs`.

| Group | Commands |
|---|---|
| `project.*` | `create`, `open`, `list`, `save`, `delete` |
| `job.*` | `submit`, `get`, `list`, `cancel`, `retry` |
| `subtitle.*` | `get_cues`, `replace_cues`, `update_cue` |
| `dictionary.glossary.*` | `list`, `upsert`, `delete`, `fingerprint` |
| `dictionary.character.*` | `list`, `upsert`, `delete` |
| `secrets.*` | `set_api_key`, `get_api_key_masked`, `delete_api_key` |
| `settings.*` | `get_all`, `set` |
| `export.*` | `video`, `subtitles` |
| `pipeline.*` | `artifact_paths` |
| system | `version` / worker status commands |

Frontend bridges live in `src/api/*.ts` (e.g. `subtitle.ts`, `project.ts`, `job.ts`, `export.ts`).

## Frontend → Rust → Worker flow in the MVP slice

```text
Projects page → project.create → job.submit(type=transcribe|translate|subtitle|render)
  → JobService → PipelineRunner → WorkerClient → worker HTTP route
  → progress/artifacts written back to the job → UI polls job.get / job.list
```