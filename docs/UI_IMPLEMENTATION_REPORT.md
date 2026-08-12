# UI Implementation Report

Date: 2026-08-12
Scope: Full UI/UX redesign of the AI Video Translation & Dubbing desktop app, wired to the existing architecture (Tauri + Rust core + Python worker + SQLite).

Implementation plan and component→IPC→Rust→worker→DB mapping: see `UI_IMPLEMENTATION_PLAN.md`.

---

## 1. UI implemented

### App shell
- `src/App.tsx` — four-route shell (`dashboard` / `automation` / `tools` / `settings`) wrapped in `JobsProvider` + `WorkerProvider`-style shared stores, `ErrorBoundary`, toast host. The legacy project detail routes (`/project/:id`) are reachable from the subtitle/export/preview tools.
- `src/components/layout/Sidebar.tsx` — **4 nav areas only** (Dashboard, Automation, Tools, Settings) under the app logo. Footer shows live **Worker status** (● Ready / Starting / Stopped / Degraded), **GPU** and **Memory** from the cached hardware probe, and the app version from `Cargo.toml`.

### Dashboard (`src/pages/Dashboard`)
- 4 stat cards fed by real state: **Worker status**, **Current job** (video, stage, progress, ETA — from the shared job store), **Today** (processed/successful/failed/total time from `job.list_all`), **System** (GPU, RAM, encoder from the hardware probe; live usage % is not exposed by the backend, so it is labelled as a static probe rather than faked).
- **Real-time processing status** — the active job with stage checklist, weighted progress bar, elapsed/ETA (elapsed is a real timer started from the job's `started_at`; ETA is derived from progress rate — never a fake counter).
- **Recent projects** + **Processing history** tables (status: Completed / Processing / Failed / Cancelled) with **Open** actions.
- **Quick actions**: New Automation → Automation route; Open Tools → Tools route.

### Automation (`src/pages/Automation`) — the core
- Dual-panel workspace: **Original video** (drop zone → `VideoPreview` player; source is never modified) and **Automated result** (idle state → live processing view → completion player for `rendered.mp4`).
- **Automation settings**: source language (incl. Auto Detect), target language, translation provider (Mock offline / Gemini / Local LLM), subtitle generation + **burn-into-video** toggle, **Branding** (text/image watermark with position, opacity, size, margin — burned by the render stage), and an explicit **Coming soon** block for voice/dubbing (TTS pipeline doesn't exist yet).
- **⚡ AUTOMATE VIDEO** — validates (video, target language, provider key when required, worker ready) then creates the project, submits the 4-stage job plan (transcribe → translate → subtitle → render) through `job.submit`, exactly like the manual Project page. Stage params are built by the pure, unit-tested `automation.ts` module.
- **Real-time processing** from the shared store: stage checklist (✓ done / ● active / ○ pending), progress bar from real per-stage `job.progress`, elapsed/ETA, **Cancel** (calls `job.cancel` → worker cancel).
- **Completion screen**: player for the rendered output, processing time, output path, buttons **Play / Open Output / Open Folder / Export** (export runs the worker's QC export path).
- **Error banner**: human-readable worker-unavailable message with **[Restart Worker]** (calls `worker.restart`).

### Tools (`src/pages/Tools`)
Capability-gated cards — only tools with a real backend are enabled:
- **Subtitle Tool** (open), **Dictionary & Glossary** (open), **Export** (open), **Preview & Overlay** (open), **Watermark** (open — routes to Automation branding) → these open the existing project detail views.
- **Voice / Dubbing**, **Audio Tool**, **Video Tool** — disabled, labelled **Coming soon** (no backend stage).

### Settings (`src/pages/Settings` + `sections.tsx`)
Sections: **General**, **AI providers** (provider select, API key → Windows Credential Manager via `settings.api_key_set/masked/delete`, base URL, connection status), **Voice** (Coming soon), **Video**, **Subtitle**, **Processing** (STT model, device, GPU override, quality preset, worker state + **Restart worker**), **Storage** (cache quota persisted), **Privacy**, **Security**, **Connection** (round-trip ping to the Rust core). Sections without backend support are shown as fixed/read-only with an explicit note — never fake controls.

### Shared state (single source of truth)
- `src/stores/jobs.tsx` — `JobsProvider`: subscribes to `job:status` events + polls `job.list_all` / `project.list`. Dashboard and Automation read the same store, so they can never drift apart.
- `src/stores/worker.ts` — one module-level polling loop (`worker.state` + `system.hardware`) feeding the sidebar dot, dashboard card, and automation banner.
- `src/api/{job,project,settings,worker,system,bridge,events,pipeline,media,export,subtitle,dictionary}.ts` — typed bridges for every IPC command used.

---

## 2. Backend integration (new/changed IPC surface)

| Command | Rust handler | Purpose |
|---|---|---|
| `job.list_all` (new) | `commands/job.rs` → `JobService::list_all` → `db/repo/job.rs` `list_all` | Full job history for Dashboard history/today stats and the shared store |
| `system.hardware` (new) | `commands/system.rs` → cached `hardware_probe.rs` `SystemInfo` | GPU/RAM/encoder for sidebar + Dashboard; cached to avoid slow probes per poll |
| `worker.restart` (new) | `commands/worker.rs` | Stop + respawn the Python sidecar from the UI |
| `pipeline_runner` render stage | `services/pipeline_runner.rs` | `params.burn_subtitles == "false"` renders without burning the ASS (previously mandatory); `params.watermark` already forwarded to `/v1/render` |
| `media` protocol | `media.rs` | Now also serves each project's working directory (`cache/`, `output/`) so the result panel can preview rendered output; still refuses anything outside project dirs |

All other actions reuse existing commands exactly: `project.create`, `job.submit`, `job.cancel`, `job.get`, `subtitle.*`, `dictionary.*`, `export.*`, `pipeline.artifact_paths`, `settings.*`, `ping`. No schema/database changes were made.

---

## 3. Features that really work (end-to-end)

- Pick a video (file dialog + drag & drop) → original player with real metadata.
- Choose source/target language + provider → create project → submit 4-stage plan → realtime progress (stage + weighted %) → rendered output previewable in the result panel → export with QC → output visible in Dashboard history.
- Burn-subtitles on/off toggle is honored by the render stage.
- Text and image watermarks: configured in Branding, serialized to the worker's `/v1/render` watermark payload, burned in by the worker's render service.
- Subtitle editor, dictionary/glossary, export, preview-overlay — the pre-existing tool views remain reachable from Tools.
- Worker status + restart, settings persistence, credential manager round-trip.

## 4. Features without backend (honestly disabled / labelled)

- **Voice selection & dubbing** — no TTS stage in the worker. UI shows "Coming soon", no fake voices, no fake preview.
- **Audio mode (preserve background music)** — not in the pipeline. Not exposed as a fake option.
- **Custom subtitle styling (font/size/color/position)** — the pipeline fixes per-language ASS defaults; the UI states this and exposes only the burn toggle.
- **Live GPU/CPU/RAM usage %** — only static hardware info is probed; labelled as such.
- **Audio Tool / Video Tool / parallel jobs / cloud processing** — disabled or read-only with notes.

## 5. Wired buttons/actions (no dead UI)

| UI action | IPC call |
|---|---|
| Choose Video / drop | `dialog` + `project.create` |
| AUTOMATE VIDEO | `project.create` + 4× `job.submit` |
| Cancel | `job.cancel` |
| Restart Worker (sidebar error, Dashboard, Settings) | `worker.restart` |
| Open project rows / recent projects | route to project detail |
| Export (Automation completion, Export tool) | `export.export_video` / `export.export_subtitles` |
| Save key / delete key / base URL | `settings.set_api_key` / `settings.delete_api_key` / `settings.set` |
| Processing settings (model/device/preset/gpu) | `settings.set` |
| Cache quota | `settings.set` (persisted) |
| Test connection | `ping` |
| Subtitle/Dictionary/Preview tools | existing project-detail views |

## 6. Tests run

- `cargo test` — **163 passed** (incl. new tests for `job.list_all`, `system.hardware`, `worker.restart`, render `burn_subtitles`, media serving).
- `cargo fmt --check` — clean.
- `npm run typecheck` — clean.
- `npm run test` — **151 passed** (incl. new tests: automation `buildStageParams`/`watermarkToWire`/`derivePhase`/`pipelineProgress`, settings sections, updated App shell test, VideoPreview).
- `npm run lint` — clean.
- `npm run build` — clean (`dist` built).
- Manual smoke test: app launched via `tauri dev` — Rust core starts, bundled worker becomes **Ready**, health check 200. All four pages render, no console errors. (In the plain-browser preview, worker shows "Stopped" and IPC-dependent values show "…" because the Tauri bridge is absent — expected; event listener and settings load now no-op cleanly outside Tauri.)

## 7. Build result

- Frontend: `vite build` ✓ (JS 336 kB / CSS 31 kB).
- Rust core: `cargo check` ✓, `cargo test` ✓.

## 8. Known limitations

- **No TTS/dubbing**: the flagship "voice" flow is gated until the worker gains a TTS stage; the Automation UI already carries the voice selector placeholder marked Coming soon.
- Browser dev/preview shows degraded states (no IPC): worker "Stopped", GPU/memory "…", no job events. This is by design and guarded so it never throws.
- Dashboard "Today" counts jobs by creation date from `job.list_all` (fine-grained per-day totals use local time).
- ETA is a progress-rate estimate; it is recalculated from real job data and may jump — never a fake timer.
- Watermark preview-on-video overlay is not rendered live in the UI; the watermark is applied at render time (as the backend supports). The Branding note says exactly that.
