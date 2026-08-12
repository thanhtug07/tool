# UI/UX Implementation — Desktop Application Workspace

**Date:** 2026-08-12
**Scope:** Final desktop product layout for the AI Video Translation & Dubbing app, wired to the existing architecture (Tauri 2 + Rust core + Python worker sidecar + SQLite). No backend redesign; every control maps to an existing IPC command or is honestly disabled/labelled.

---

## 1. UI architecture

- **Shell:** `src/App.tsx` — a four-area desktop shell (`dashboard` / `automation` / `tools` / `settings`) wrapped in `JobsProvider` (shared job+project store), `ErrorBoundary`, toast host and `JobFailBanner`.
- **Navigation:** `src/components/layout/Sidebar.tsx` — exactly four nav areas. Workspace group (**Dashboard, Automation, Tools**) is separated from **Settings** by a divider. **Automation** carries visual emphasis (accent ring + ⚡ "Core" badge) because it is the app's core workflow. The footer shows real system state from the shared worker store (worker status dot, GPU name, RAM, version) — never faked.
- **Shared state (single source of truth):**
  - `src/stores/jobs.tsx` — `JobsProvider` subscribes to `job:status` events and polls `job.list_all` / `project.list`. Dashboard and Automation read the same store, so they can never drift apart.
  - `src/stores/worker.ts` — one module-level polling loop (`worker.get_worker_state` + `system.hardware`) feeding the sidebar, dashboard cards and automation banner.
- **Design primitives:** `src/components/ui/status.tsx` — `StatusDot`, `StatusBadge`, `ProgressBar` (determinate, real values only), used consistently across Sidebar/Dashboard/Automation. Buttons reuse `src/components/ui/button.tsx`; the existing dark token theme in `globals.css` is unchanged.

## 2. Page structure

### Dashboard (`src/pages/Dashboard`)
Observation + quick action only — not a processing workspace.
- **Header** + `[ + New Automation ]` quick action.
- **System status** — four compact cards from real data: Worker (state/PID/port), GPU (name, RAM/VRAM from the cached hardware probe), AI providers (translation: mock ready / Gemini key stored / local LLM URL set; STT: faster-whisper local; TTS: not in this build), Storage (cache quota setting; live free/used disk is **not** exposed by the backend and is labelled as such).
- **Real-time processing status** — active job (project, stage, weighted progress, elapsed from `started_at`) plus a **real 5-stage checklist** (Extract audio → Speech-to-text → Translate → Subtitles → Render) derived from the active project's job rows and the worker's live `stage` string, followed by honest "later" lines for voice/mixing/logo. ETA is not fabricated — the UI states that the backend reports stages, not ETA.
- **Recent projects** — table (Video, Language, Status, Processing time, Created, Open). Duration is not stored by the pipeline and is shown honestly as "—".
- **Processing history** — compact list of recent jobs with status chips and today's summary.
- Quick actions: New Automation, Open Tools, View running job.

### Automation (`src/pages/Automation`) — the core
- **Dual-panel workspace:** `Original video` (drop zone → `VideoPreview`; metadata: file/resolution/duration from the media element; FPS is not exposed and shown honestly as "—") and `Automated result` (idle empty state → live processing view → completion player for `rendered.mp4`). The settings rail sits beside the panels (3-column layout on wide viewports, stacked below on smaller ones — no long scroll).
- **Essential settings (always visible):** Source language (incl. Auto Detect), Target language, Voice & dubbing (honest "Later" block — no fake voices), Subtitles (Translate + Generate = core and fixed-on; Burn-into-video toggle), Output (MP4/H.264, preserves source), Options (Dub audio / Preserve background music / Remove logo — **disabled** checkboxes with the reason, since those stages are not in this build).
- **Advanced options (collapsed by default):** Translation provider (Mock / Gemini / Local LLM with key status + "Configure API key") and Branding (text/image watermark burned by the render stage).
- **⚡ Automate Video** — validates (video present, worker ready, provider key when required) then submits the real 4-stage plan (`transcribe → translate → subtitle → render`) via `job.submit`; stage params come from the pure, unit-tested `automation.ts`.
- **Realtime processing** — stage checklist (✓/●/○) expanded to the 5 real worker stages, per-stage `job.progress` weighted across the pipeline, elapsed timer, Cancel (`job.cancel`), and an honest "later" section for the not-yet-built stages.
- **Completion** — result player with Original/Result toggle, processing time, output path, actions **Export** (worker QC export), **Copy path**, **Reprocess**, **Edit subtitles** (→ Tools). "Open in folder" is intentionally **not** rendered — there is no reveal-in-Explorer backend command and the task forbids fake buttons.

### Tools (`src/pages/Tools`)
- Tool workspace with a category layout: **Video** (Export, Preview & Overlay, Watermark→Automation), **Audio** (Audio Extractor→Automation), **Subtitle** (Subtitle Editor, Subtitle Generator→Automation), **AI** (Speech-to-Text→Automation, Dictionary & Glossary, Translation→Automation).
- Tools whose backend stage does not exist yet are **not** rendered as buttons — a single compact "Planned — not in this build" panel lists them (Video Cutter, Video Converter, Logo Remover, Audio Separator, Audio Mixer, Voice Generator, Voice Dubbing) with a "later" label.
- Tools that run inside the automation pipeline navigate to Automation with a "Runs in Automation" badge — honest routing, no duplicate workspace.

### Settings (`src/pages/Settings`)
Reorganized into the five product groups:
1. **General** — app-wide preferences (theme/language fixed + noted) + Privacy.
2. **AI providers** — provider select, API key → Windows Credential Manager (masked display, never the secret), base URL, connection status, honest STT/TTS provider rows.
3. **Audio & video** — Voice (Coming soon), Video, Subtitle (fixed per-language style noted).
4. **Storage** — cache quota (persisted via `settings.set`), output/models locations.
5. **Advanced** — Processing (STT model, device, GPU override, preset, worker restart), Security, Connection (round-trip `ping`).

## 3. Component structure

```
src/
  components/
    layout/Sidebar.tsx        # 4-area nav + real system footer
    ui/button.tsx             # existing button variants
    ui/status.tsx             # StatusDot / StatusBadge / ProgressBar
    ui/utils.ts               # cn()
    VideoPreview.tsx          # player + caption overlay (unchanged contract)
    WatermarkConfig.tsx       # watermark form (burned at render time)
  pages/
    Dashboard/                # system status + processing + recent + history
    Automation/               # dual panels + settings rail + live pipeline
      automation.ts           # pure stage params/plan/checklist logic (tested)
    Tools/                    # category workspace + planned list
    Settings/                 # 5 groups + sections.tsx
  stores/                     # jobs.tsx, worker.ts (shared truth)
  api/                        # typed IPC bridges
  lib/                        # pipeline/format/env helpers
```

## 4. Existing backend mapping (every wired control)

| UI action | IPC / backend |
|---|---|
| Choose Video / drag-drop | `dialog` plugin + `project.create` |
| AUTOMATE VIDEO | `project.create` + 4× `job.submit` (transcribe/translate/subtitle/render) |
| Cancel | `job.cancel` |
| Retry / Reprocess | `job.retry` / re-submit plan |
| Restart Worker | `worker.restart` |
| Export (Automation + Export tool) | `export.video` / `export.subtitles` (worker QC) |
| Edit subtitles / preview / export tools | existing project-detail views (`subtitle.*`, `dictionary.*`, `pipeline.artifact_paths`) |
| Source/target language, provider, burn toggle, watermark | serialized into `job.submit` params (`automation.ts`) |
| Settings (model/device/preset/gpu/cache/privacy) | `settings.set` |
| API key save/remove, masked read | `secrets.set_api_key` / `delete_api_key` / `get_api_key_masked` (Credential Manager) |
| Test connection | `ping` |
| Worker/GPU/RAM/encoder displays | `worker.get_worker_state` + `system.hardware` |

## 5. Features actually functional

Drop a video → probe-able original player → choose languages/provider → Automate → real per-stage progress (worker stage strings + per-job progress) → rendered video previewable in the result panel → Export with QC → history on the Dashboard. Burn-toggle and text/image watermarks are honored by the render stage. Worker status/restart, settings persistence and the credential-manager round-trip are real.

## 6. Features intentionally hidden / disabled (no fake functionality)

- **Voice selection & dubbing** — no TTS stage; shown as "Later", no voices, no preview, no fake progress.
- **Audio separation / background-music preservation** — not in the pipeline; disabled options with reason.
- **Remove logo / watermark** — requires OCR + inpainting; disabled option with reason.
- **Custom subtitle styling** — pipeline fixes per-language ASS defaults; the UI states this and exposes only the burn toggle.
- **Live CPU/GPU/RAM usage % and free/used disk** — no backend endpoint; labelled as unavailable instead of faked.
- **ETA** — not fabricated; the UI reports elapsed + stage and notes the backend does not expose ETA.
- **Open in folder** — no reveal command exists; the action is omitted (Copy path + Export instead).
- **Planned tools** (cutter/converter/logo remover/separator/mixer/voice generator/dubbing) — listed in a "planned" panel, never clickable.

## 7. Remaining UI limitations

- Duration column on the Dashboard and FPS/codec on the Automation metadata panel are honest "—" because the backend does not expose a media-probe over IPC (the worker's `media_service` has no HTTP route).
- Free/used disk and live usage % require new backend endpoints before the Storage/GPU cards can show them.
- ETA and per-second processing speed need worker progress-rate reporting before they can be shown.
- The 1280×720 viewport shows the two Automation panels and the settings rail by stacking below `xl` breakpoints; the essential settings and the Automate button remain visible without scrolling.
- Dubbing remains the single biggest product gap: the entire voice pipeline (TTS, audio mixing, alignment, music preservation) is not implemented, so the final output is translated + subtitled + watermarked, not dubbed.

## 8. Gates

- `npm run typecheck` — PASS
- `npm run lint` — PASS
- `npm run format:check` — PASS (changed files)
- `npm run test` — PASS (151 tests, 23 files)
- `npm run build` — PASS (vite production build)
