//! Library entry for the Tauri Rust core.
//! TASK-001: foundation scaffold. TASK-004: first typed IPC command
//! (`system::ping`). TASK-006: Python sidecar lifecycle — the worker starts at
//! app startup, reaches `Ready`, and is gracefully shut down on app exit.
//! TASK-008: SQLite + ProjectService — the database lives in the OS user-data
//! directory and project CRUD is exposed over `project.*` IPC commands.
//! TASK-010: JobService — the pipeline job orchestrator exposed over `job.*`
//! IPC; its `job:status`/`job:log` events are forwarded to the frontend.

pub mod commands;
pub mod db;
pub mod logging;
pub mod media;
pub mod security;
pub mod services;

use std::sync::Arc;

use tauri::{Emitter, Manager, WindowEvent};

use security::secret_store::SecretStore;
use services::cache_service::{CacheService, CacheServiceConfig};
use services::dictionary_service::DictionaryService;
use services::job_service::{JobEvent, JobEventSink, JobService, JobServiceConfig, NotWiredRunner};
use services::project_service::ProjectService;
use services::settings_service::SettingsService;
use services::subtitle_service::SubtitleService;
use services::worker_manager::{WorkerManager, WorkerManagerConfig};

/// Forwards `JobEvent`s to the frontend as Tauri events (`job:status`,
/// `job:log` — MASTER_PLAN.md §25.2).
struct AppEventSink {
    app: tauri::AppHandle,
}

impl JobEventSink for AppEventSink {
    fn emit(&self, event: JobEvent) {
        let _ = match event {
            JobEvent::Status(payload) => self.app.emit("job:status", payload),
            JobEvent::Log(payload) => self.app.emit("job:log", payload),
        };
    }
}

pub fn run() {
    logging::init();

    tauri::Builder::default()
        // Scoped `media://` protocol for the video preview (TASK-026): only a
        // registered project's source video is streamed, never arbitrary files.
        .register_uri_scheme_protocol(media::MEDIA_SCHEME, |ctx, request| {
            media::media_response(ctx.app_handle(), &request)
        })
        .manage(WorkerManager::new(WorkerManagerConfig::default()))
        .invoke_handler(tauri::generate_handler![
            commands::system::ping,
            commands::worker::get_worker_state,
            commands::project::create,
            commands::project::open,
            commands::project::save,
            commands::project::delete,
            commands::job::submit,
            commands::job::get,
            commands::job::list,
            commands::job::cancel,
            commands::job::retry,
            commands::dictionary::glossary_list,
            commands::dictionary::glossary_upsert,
            commands::dictionary::glossary_delete,
            commands::dictionary::glossary_fingerprint,
            commands::dictionary::character_list,
            commands::dictionary::character_upsert,
            commands::dictionary::character_delete,
            commands::subtitle::get_cues,
            commands::subtitle::replace_cues,
            commands::subtitle::update_cue,
            commands::export::export_video,
            commands::export::export_subtitles,
            commands::settings::set_api_key,
            commands::settings::get_api_key_masked,
            commands::settings::delete_api_key,
            commands::settings::get_all,
            commands::settings::set,
        ])
        .setup(|app| {
            // The app keeps running even if the worker cannot start (e.g. no
            // Python on PATH in a frontend-only dev environment); the failure
            // is surfaced through `get_worker_state`.
            if let Err(e) = app.state::<WorkerManager>().start() {
                log::error!("worker failed to start: {e}");
            }

            // Project database in the OS user-data dir (never the source tree).
            // `ProjectService::open` captures init failures internally, so the
            // app still runs and `project.*` commands report the error cleanly.
            let data_dir = app.path().app_data_dir()?;
            app.manage(ProjectService::open(data_dir.clone()));

            // Job orchestrator over the same `app.db` (WAL allows concurrent
            // connections). TASK-010 ships the lifecycle with a placeholder
            // runner; concrete executors are wired by later pipeline tasks.
            app.manage(JobService::open(
                data_dir.clone(),
                Arc::new(NotWiredRunner),
                Arc::new(AppEventSink {
                    app: app.handle().clone(),
                }),
                JobServiceConfig::default(),
            ));

            // Content-addressed cache over the same DB (TASK-011). Quota LRU
            // with the frozen 10 GB default (ARCHITECTURE_DECISION.md §3.7).
            app.manage(CacheService::open(
                data_dir.clone(),
                CacheServiceConfig::default(),
            ));

            // Project-scoped glossary + character dictionary over the same DB
            // (TASK-023). The glossary fingerprint feeds the worker's
            // translation-memory versioning.
            app.manage(DictionaryService::open(data_dir.clone()));

            // Project-scoped subtitle cue persistence (TASK-025): the editor
            // reads/edits cues; the worker's SubtitleEngine output replaces a
            // project's cues atomically via `subtitle.replace_cues`.
            app.manage(SubtitleService::open(data_dir.clone()));

            // API keys → OS credential vault (TASK-030, FIX #8): never in the
            // SQLite DB, never in files, never logged. Saves are blocked with a
            // clear error when the credential service is unavailable.
            app.manage(SecretStore::new());

            // Whitelisted non-secret app settings (TASK-030).
            app.manage(SettingsService::open(data_dir.clone()));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                window.app_handle().state::<WorkerManager>().stop();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Belt-and-suspenders: guarantee cleanup even if a window close was
            // not observed. `stop` is idempotent.
            if let tauri::RunEvent::Exit = event {
                if let Some(manager) = app_handle.try_state::<WorkerManager>() {
                    manager.stop();
                }
                if let Some(jobs) = app_handle.try_state::<JobService>() {
                    jobs.stop();
                }
            }
        });
}
