# UI_IMPLEMENTATION_PLAN.md

**Phạm vi:** Redesign toàn bộ UI/UX của app theo spec "Desktop AI Video Translation & Dubbing".
**Nguyên tắc:** Mọi nút chính phải có action thật. Feature chưa có backend → disable + ghi rõ. Không fake progress / provider / state.

## 1. Capability map — Backend thực sự có gì

### Tauri IPC (frontend → Rust)

| Group          | Commands                                                       | Dùng cho                                                |
| -------------- | -------------------------------------------------------------- | ------------------------------------------------------- |
| `project.*`    | `create`, `open`, `list`, `save`, `delete`                     | Automation import, Dashboard recent projects            |
| `job.*`        | `submit`, `get`, `list`, `cancel`, `retry` (+ mới: `list_all`) | Automation pipeline, Dashboard history, Tools           |
| `subtitle.*`   | `get_cues`, `replace_cues`, `update_cue`                       | Tools → Subtitle Editor                                 |
| `dictionary.*` | glossary + character CRUD                                      | Tools → Dictionary                                      |
| `secrets.*`    | `set/get_masked/delete_api_key`                                | Settings → AI Providers (key vault)                     |
| `settings.*`   | `get_all`, `set`                                               | Settings                                                |
| `export.*`     | `video`, `subtitles`                                           | Automation → Export, Tools → Export                     |
| `pipeline.*`   | `artifact_paths`                                               | Automation result panel, preview, export                |
| `system`       | `ping` (+ mới: `hardware`)                                     | Settings connection test, Sidebar/Dashboard system card |
| `worker.*`     | `get_worker_state` (+ mới: `restart`)                          | Sidebar worker status, Dashboard worker card, error UX  |

### Job pipeline (thật)

- Job types: `transcribe` → `translate` → `subtitle` → `render` (FIFO single worker).
- Status: `queued / running / succeeded / failed / cancelled`.
- Progress: 0..1 + `stage` string (`extract-audio`, `transcribe`, `translate`, `subtitle`, `render`, `done`).
- Events: `job:status`, `job:log` (realtime).
- `transcribe` params: `video_path`, `language`, `model`, `device`.
- `translate` params: `provider` (mock|gemini|local), `target_language`, `model`.
- `subtitle` params: `language`.
- `render` params: `output_name`, `encoder`, `preset`, `crf`, `watermark {text|image}`, (+ mới `burn_subtitles`).

### Không có backend (phải disable / "Coming soon")

- TTS / dubbing / voice selection — KHÔNG có job type `tts`, không có voice registry.
- Audio mixing / music preservation — không có stage.
- Subtitle style override — worker subtitle stage không nhận style (dùng default theo ngôn ngữ).
- Video trim/merge, audio separation — không có stage.
- System usage % (CPU/GPU/RAM/VRAM/disk real-time) — không có endpoint; chỉ có hardware probe tĩnh (GPU name/VRAM, RAM).
- Thumbnail tự sinh, media duration/audio-track metadata — không có endpoint media probe; chỉ lấy từ `<video>` element.
- Mở folder output bằng shell — chưa có tauri-plugin-shell (không thêm dep mới); thay bằng "Copy path".

## 2. UI component → action mapping

| UI component                     | Frontend action                       | IPC                                    | Rust                                            | Worker                  | DB state                  |
| -------------------------------- | ------------------------------------- | -------------------------------------- | ----------------------------------------------- | ----------------------- | ------------------------- |
| Sidebar nav                      | set active page                       | —                                      | —                                               | —                       | —                         |
| Sidebar worker status            | poll `worker.get_worker_state`        | `worker.get_worker_state`              | `WorkerManager.state_info`                      | —                       | —                         |
| Sidebar GPU/RAM                  | poll `system.hardware`                | `system.hardware`                      | `SystemInfo` → `hardware_probe::probe` (cached) | —                       | —                         |
| Dashboard WORKER STATUS card     | poll worker                           | `worker.get_worker_state`              | `WorkerManager`                                 | —                       | —                         |
| Dashboard CURRENT JOB            | shared jobs store                     | `job.list_all` + `job:status`          | `JobService.list_recent`                        | —                       | `jobs` rows               |
| Dashboard TODAY stats            | derived from jobs                     | `job.list_all`                         | —                                               | —                       | `jobs` (created/finished) |
| Dashboard SYSTEM card            | `system.hardware`                     | `system.hardware`                      | probe                                           | —                       | —                         |
| Dashboard REAL-TIME PROCESSING   | shared jobs store                     | `job.list_all` + events                | `JobService`                                    | —                       | `jobs`                    |
| Dashboard RECENT PROJECTS        | `project.list`                        | `project.list`                         | `ProjectService.list`                           | —                       | `projects`                |
| Dashboard PROCESSING HISTORY     | shared jobs store                     | `job.list_all`                         | `JobService.list_recent`                        | —                       | `jobs`                    |
| Automation: Choose/Drop video    | `open` dialog / webview drag-drop     | dialog + `project.create`              | `ProjectService.create`                         | —                       | `projects`                |
| Automation: original preview     | `toMediaUrl(source)`                  | media:// protocol                      | `media.rs` (source video)                       | —                       | —                         |
| Automation: source language      | select (auto + zh/en/ja/ko/vi)        | params `language` của transcribe       | `PipelineRunner.run_transcribe`                 | STT detect              | `jobs.params`             |
| Automation: target language      | select (vi/en/zh/ja/ko)               | params `target_language` của translate | `PipelineRunner.run_translate`                  | translate provider      | `jobs.params`             |
| Automation: translation provider | select mock/gemini/local + key status | `secrets.get_api_key_masked`           | `SecretStore`                                   | provider registry       | vault                     |
| Automation: burn subtitles       | checkbox                              | params `burn_subtitles` của render     | `PipelineRunner.run_render`                     | render                  | `jobs.params`             |
| Automation: watermark            | `WatermarkConfig` + image picker      | params `watermark` của render          | `PipelineRunner.run_render`                     | `/v1/render` watermark  | `jobs.params`             |
| Automation: voice/dubbing        | disabled + "Coming soon"              | —                                      | —                                               | —                       | —                         |
| Automation: subtitle style       | disabled + note                       | —                                      | —                                               | default style theo lang | —                         |
| AUTOMATE button                  | validate → submit 4 stages tuần tự    | `job.submit` ×4                        | `JobService.submit` → runner                    | 4 stage routes          | `jobs`                    |
| Processing progress/stage list   | shared jobs store + events            | `job.list_all`/`job:status`            | `JobService.report_progress`                    | stage progress          | `jobs.progress/stage`     |
| Cancel                           | `job.cancel`                          | `job.cancel`                           | `JobService.cancel`                             | `/v1/jobs/{id}/cancel`  | `jobs`                    |
| Completion result preview        | `toMediaUrl(renderedVideo)`           | media:// protocol                      | `media.rs` (project artifacts)                  | —                       | —                         |
| Export                           | `export.video`                        | `export.video`                         | worker proxy                                    | `/v1/export/video`      | —                         |
| Open Folder                      | "Copy path" (no shell dep)            | —                                      | —                                               | —                       | —                         |
| Reprocess                        | re-run pipeline                       | `job.submit` ×4                        | `JobService`                                    | stages                  | `jobs`                    |
| Edit (subtitles)                 | navigate Tools→Subtitle Editor        | `subtitle.get_cues`/`update_cue`       | `SubtitleService`                               | —                       | `subtitle_cues`           |
| Worker unavailable banner        | `worker.get_worker_state`             | `worker.get_worker_state`              | `WorkerManager`                                 | —                       | —                         |
| Restart Worker                   | `worker.restart`                      | `worker.restart` (mới)                 | stop + start                                    | —                       | —                         |
| API key missing                  | link Settings                         | `secrets.get_api_key_masked`           | `SecretStore`                                   | —                       | vault                     |
| Tools cards                      | per-card view                         | các command tương ứng                  | —                                               | —                       | —                         |
| Settings AI PROVIDERS            | `settings.get_all/set` + secrets      | như trên                               | `SettingsService`/`SecretStore`                 | —                       | `settings`, vault         |
| Settings PROCESSING              | `settings.set` + worker               | như trên                               | —                                               | —                       | `settings`                |
| Settings PRIVACY                 | `settings.set`                        | như trên                               | —                                               | —                       | `settings`                |

## 3. Backend additions (minimal, không đổi schema)

| Thêm                              | Lý do                                                              | File                                                         |
| --------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| `job.list_all`                    | Dashboard history / current job — 1 nguồn sự thật                  | `db/repo/job.rs`, `job_service.rs`, `commands/job.rs`        |
| `system.hardware`                 | Sidebar + Dashboard SYSTEM card hiển thị GPU/RAM thật              | `hardware_probe.rs` (SystemInfo cache), `commands/system.rs` |
| `worker.restart`                  | Error UX "Restart Worker" có action thật                           | `commands/worker.rs`                                         |
| `burn_subtitles` param            | Checkbox "Burn subtitles" thật (render không gửi subtitle khi tắt) | `services/pipeline_runner.rs`                                |
| media:// cho artifact của project | Preview rendered video / audio (không mở file ngoài project)       | `media.rs`                                                   |

## 4. Shared state — Dashboard & Automation cùng nguồn sự thật

- `src/stores/jobs.tsx`: `JobProvider` subscribe `job:status` + poll `job.list_all` (3s) → `useJobs()` trả về toàn bộ jobs. Dashboard (CURRENT JOB / REAL-TIME) và Automation (stage checklist / progress) đọc cùng dữ liệu này.
- `src/stores/worker.ts`: poll `worker.get_worker_state` + `system.hardware` (cache) → Sidebar / Dashboard / Automation error UX.

## 5. Quyết định UX (không fake)

- Progress: dùng `job.progress` thật + `stage` thật; hiển thị stage-based (elapsed tính từ `started_at` thật). KHÔNG hiển thị ETA giả → ghi "—".
- Stage checklist: 5 bước thật (Extract audio, Transcribe, Translate, Subtitles, Render).
- Voice/dubbing: disabled "Coming soon — requires the TTS pipeline".
- Subtitle style: disabled, note "pipeline uses per-language defaults".
- Thumbnail: không sinh ảnh giả → dùng placeholder icon.
- "Open Folder": không có shell dep → "Copy path" (clipboard thật).
