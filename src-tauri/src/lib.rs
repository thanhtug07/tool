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
use services::hardware_probe::SystemInfo;
use services::job_service::{JobEvent, JobEventSink, JobService, JobServiceConfig};
use services::pipeline_runner::PipelineRunner;
use services::project_service::ProjectService;
use services::provider_service::ProviderService;
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
        // Native file-open dialog for the video import flow (RELEASE-P0-005).
        .plugin(tauri_plugin_dialog::init())
        // Scoped `media://` protocol for the video preview (TASK-026): only a
        // registered project's source video is streamed, never arbitrary files.
        .register_uri_scheme_protocol(media::MEDIA_SCHEME, |ctx, request| {
            media::media_response(ctx.app_handle(), &request)
        })
        .invoke_handler(tauri::generate_handler![
            commands::system::ping,
            commands::system::hardware,
            commands::worker::get_worker_state,
            commands::worker::restart,
            commands::project::create,
            commands::project::open,
            commands::project::list_projects,
            commands::project::save,
            commands::project::delete,
            commands::job::submit,
            commands::job::get,
            commands::job::list,
            commands::job::list_all,
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
            commands::pipeline::artifact_paths_command,
            commands::settings::set_api_key,
            commands::settings::get_api_key_masked,
            commands::settings::delete_api_key,
            commands::settings::get_all,
            commands::settings::set,
            commands::provider::list_providers,
            commands::provider::get_provider,
            commands::provider::create_provider,
            commands::provider::update_provider,
            commands::provider::delete_provider,
            commands::provider::set_provider_default,
            commands::provider::set_provider_enabled,
            commands::provider::test_provider,
        ])
        .setup(|app| {
            // Release-mode packaging (MASTER_PLAN §32): when the bundled
            // worker + FFmpeg resources are present next to the binary, the
            // worker is spawned from the bundle — no Python on PATH, no
            // source tree. Dev mode falls back to `python -m src.main`.
            let mut worker_config = WorkerManagerConfig::default();
            let resource_dir = app.path().resource_dir().ok();
            if let Some(res) = &resource_dir {
                let bundled = res.join("worker/worker.exe");
                if bundled.is_file() {
                    log::info!("release mode: bundled worker at {}", bundled.display());
                    worker_config.worker_bin = Some(bundled);
                    worker_config.resource_dir = Some(res.clone());
                }
            }
            app.manage(WorkerManager::new(worker_config));

            // The app keeps running even if the worker cannot start (e.g. no
            // Python on PATH in a frontend-only dev environment); the failure
            // is surfaced through `get_worker_state`.
            if let Err(e) = app.state::<WorkerManager>().start() {
                log::error!("worker failed to start: {e}");
            }

            // Cached hardware snapshot (probed lazily on first `system.hardware`).
            app.manage(Arc::new(SystemInfo::new()));

            // Project database in the OS user-data dir (never the source tree).
            // `ProjectService::open` captures init failures internally, so the
            // app still runs and `project.*` commands report the error cleanly.
            // All services are shared with the pipeline runner via `Arc`.
            let data_dir = app.path().app_data_dir()?;
            let projects = Arc::new(ProjectService::open(data_dir.clone()));
            let settings = Arc::new(SettingsService::open(data_dir.clone()));
            let secrets = Arc::new(SecretStore::new());
            let subtitles = Arc::new(SubtitleService::open(data_dir.clone()));
            let dictionary = Arc::new(DictionaryService::open(data_dir.clone()));
            let providers = Arc::new(ProviderService::open(data_dir.clone()));
            let cache = Arc::new(CacheService::open(
                data_dir.clone(),
                CacheServiceConfig::default(),
            ));

            // Job orchestrator over the same `app.db` (WAL allows concurrent
            // connections). RELEASE-P0-003 wires the real pipeline executor:
            // each job type dispatches to the Python worker stages through the
            // loopback HTTP API, with per-project artifacts.
            app.manage(JobService::open(
                data_dir.clone(),
                Arc::new(PipelineRunner::new(
                    Arc::new(app.state::<WorkerManager>().inner().clone()),
                    projects.clone(),
                    settings.clone(),
                    secrets.clone(),
                    subtitles.clone(),
                    dictionary.clone(),
                    providers.clone(),
                )),
                Arc::new(AppEventSink {
                    app: app.handle().clone(),
                }),
                JobServiceConfig::default(),
            ));

            app.manage(projects);
            app.manage(settings);
            app.manage(secrets);
            app.manage(subtitles);
            app.manage(dictionary);
            app.manage(providers);
            app.manage(cache);
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
