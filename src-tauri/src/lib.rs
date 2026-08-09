//! Library entry for the Tauri Rust core.
//! TASK-001: foundation scaffold. TASK-004: first typed IPC command
//! (`system::ping`). TASK-006: Python sidecar lifecycle — the worker starts at
//! app startup, reaches `Ready`, and is gracefully shut down on app exit.

pub mod commands;
pub mod logging;
pub mod services;

use tauri::{Manager, WindowEvent};

use services::worker_manager::{WorkerManager, WorkerManagerConfig};

pub fn run() {
    logging::init();

    tauri::Builder::default()
        .manage(WorkerManager::new(WorkerManagerConfig::default()))
        .invoke_handler(tauri::generate_handler![
            commands::system::ping,
            commands::worker::get_worker_state,
        ])
        .setup(|app| {
            // The app keeps running even if the worker cannot start (e.g. no
            // Python on PATH in a frontend-only dev environment); the failure
            // is surfaced through `get_worker_state`.
            if let Err(e) = app.state::<WorkerManager>().start() {
                log::error!("worker failed to start: {e}");
            }
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
            }
        });
}
