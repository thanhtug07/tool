# SECURITY.md

Security model of the **AI Video Localization Studio** (Tauri shell + Python worker). Required doc per `MASTER_PLAN.md §22/§44`. Maps to `MASTER_PLAN §20`, `ARCHITECTURE_DECISION.md §6`.

## Threat model summary

A local-first desktop tool whose only network ingress is loopback HTTP and only optional egress is user-configured cloud AI APIs. Primary assets: the source video, transcripts, API keys, and the packaged binaries. No telemetry, no account system.

## Secrets

- **API keys live in the OS credential store** (`src-tauri/src/security/secret_store.rs`) via `keyring` → Windows Credential Manager. Never in SQLite, never in files, never logged.
- **FIX #8 fail-safe:** if the credential service is unavailable, key _save is blocked_ — there is **no** file/crypto fallback and no plaintext-on-disk path.
- Provider names are validated against a fixed allow-list before touching the vault (no attacker-controlled `service`/`user`).
- IPC only exposes the key **masked** (`AIz****wxyz`); the full key is never sent to the renderer UI.
- Secrets never appear in: `git`, `.env` (gitignored), logs, argv (the token handshake uses stdin).

## Worker isolation & IPC

- Worker binds **`127.0.0.1` only** (no LAN/WAN); one **per-session bearer token** is passed over stdin (`WORKER_AUTH_TOKEN=<token>` line), the worker echoes `READY <token>`, and every HTTP call requires `Authorization: Bearer` (`worker/src/main.py`, `worker/src/api/routes.py`).
- Token never appears in process args or logs.
- Worker runs as a **separate process** (crash isolation from the Tauri core).

## Renderer / Tauri hardening

- Capabilities: `core:default` + `dialog:default` only — **deny-by-default**, no broad filesystem/shell/process/http grants (`src-tauri/capabilities/default.json`).
- Strict CSP (`src-tauri/tauri.conf.json`): `default-src 'self'`, `script-src 'self'`, no remote sources, no `unsafe-eval` in production. Dev CSP relaxes only `script/style` with `'unsafe-inline'` for the Vite dev server.

## Files

- UUID validation guards path/traversal in SQLite bindings.
- Export writes are atomic (temp + `os.replace`) with a directory write-probe; unwritable targets fail with `E_PERMISSION_DENIED` before any partial file is left.
- **Shell execution:** ffmpeg/ffprobe/whisper-cli are invoked as argument arrays only (no `shell=True`/`os.system`); binaries come from an allow-listed resolver; filter-graph paths are avoided by copying subtitle/watermark files into a temp workdir under generated names.
- Temp files are cleaned up on job completion **and** cancellation (integration-tested for audio + render).
- Model downloads (faster-whisper etc.) are SHA-256 verified before a model is marked ready.

## Privacy mode

Default: STT/FFmpeg/subtitle/render run fully local — nothing is uploaded. Cloud translation is used only when the user explicitly enables it; if a local model is configured it is preferred. No telemetry unless the user opts in. (Note: enforcement is wired through the settings record; it becomes load-bearing once the pipeline executes — see `RELEASE_READINESS_AUDIT.md`.)

## Data & logging

- **Never logged:** API keys, password, tokens, sensitive transcript contents (log lengths/hashes when debugging instead). Session logs are bounded.
- Pipelines log job-level events (code + message), not raw media content.

## Secret scanning

- `gitleaks detect` is configured as a CI security job; `.gitignore` blocks `.env`, `*.key`, `*.pfx`, `*.pem`, `*.p12`. (A local `gitleaks` run is pending — tool not installed on the dev machine; see `RELEASE_READINESS_AUDIT.md`.)

## Verified vs pending

Per the release audit: the design is sound and consistent with `MASTER_PLAN §20`; a real OS-credential-store roundtrip and a local `gitleaks` run are only testable on the production OS setup. No security finding can be claimed from a local gitleaks run until it is executed.
