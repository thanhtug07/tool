# DATABASE.md

SQLite database design for the Tauri core (`src-tauri/src/db/`). Required doc per `MASTER_PLAN.md §22/§44`.

## Engine & location

- SQLite via `rusqlite` (bundled), WAL mode, local to the app data directory (path handled in `src-tauri/src/db/mod.rs`).
- Schema is **versioned** via `PRAGMA user_version`; migrations are applied once, in order, inside `IMMEDIATE` transactions so concurrent app instances serialize (`src-tauri/src/db/migrations.rs`).
- Rule: the migration list is **append-only**; applied migrations are never edited.

## Schema (current version 7)

| Table               | Purpose                               | Key columns                                                                                                                                                                                                      |
| ------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `projects`          | Project records                       | `id` (uuid v4), `name`, `source_video_path`, `status` (draft/analyzed/transcribed/translated/rendered), `settings_json`                                                                                          |
| `jobs`              | Pipeline job records + progress/state | `id` (`job_NNNN`), `project_id` FK cascade, `type` (transcribe/translate/subtitle/render), `status`, `progress`, `stage`, `error_code/message/log`, `params_json`, `retry_count`, `cancel_requested`, timestamps |
| `cache_entries`     | Content-addressed stage cache         | `(project_id, key)` PK, `stage`, `file_name`, `size_bytes`, `last_accessed_at`                                                                                                                                   |
| `glossary_entries`  | Glossary term→translation             | `(project_id, term)` PK                                                                                                                                                                                          |
| `character_entries` | Character/context descriptions        | `(project_id, name)` PK                                                                                                                                                                                          |
| `subtitle_cues`     | Persistent subtitle cue rows          | `id` (uuid v4), `(project_id, cue_number)` unique, `start/end` (s), `text`, `speaker`, `source_text`, `status`, `style_json`                                                                                     |
| `settings`          | App settings (whitelisted keys)       | `key` PK, `value`                                                                                                                                                                                                |

Indexes exist for the common access paths (`projects.updated_at`, `jobs.project_id`, `jobs.status`, `cache_entries` by last-access/stage, per-project rows on glossary/characters/cues).

## Cache keys

`cache_entries.key` uses the canonical content-addressed key from `MASTER_PLAN §3.7`:

- audio: `audio:<video_sha256>:<extract_spec>`
- STT: `stt:<audio_sha256>:<model>:<compute>:<lang>:<vad>`
- translation: `tr:<source_sha256>:<target>:<model>:<glossary_ver>:<rules_ver>`
- subtitle: `subtitle:<...>`
- render: `render:<video_sha256>:<style>:<watermark>:<encoder>:<preset>` (watermark fingerprint includes text/image content hash)

## Access layers

- `src-tauri/src/db/mod.rs` — connection, migrations runner, `PRAGMA user_version` diagnostics.
- `src-tauri/src/db/repo/*.rs` — per-domain repositories (`project`, `job`, `glossary`, ...) with `DbError` mapping.
- IPC commands (`src-tauri/src/commands/*.rs`) are thin wrappers over repositories/services.

## Security

- UUID v4 validation guards against path/bind-injection; FKs use `ON DELETE CASCADE`.
- API keys are **not** stored in the database — they live in the OS credential store (see `SECURITY.md`).

## Tests

- `src-tauri/src/db/migrations.rs` tests cover fresh-DB reach-latest-version, `user_version` gating, and re-application safety.
- Repo-level tests cover project/job/glossary/subtitle CRUD roundtrips.
