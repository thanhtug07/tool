//! Worker lifecycle IPC (TASK-006).
//!
//! Exposes only the *state* of the Python sidecar — never the session token.
//! The token is owned by the Rust core and stays internal (no IPC, no UI, no
//! logs). Later worker API calls are proxied through controlled Rust commands.

use tauri::State;

use crate::services::worker_manager::{WorkerManager, WorkerStateInfo};

/// Returns the current lifecycle snapshot of the Python sidecar.
#[tauri::command]
pub fn get_worker_state(manager: State<'_, WorkerManager>) -> WorkerStateInfo {
    manager.state_info()
}

/// `worker.restart() → WorkerStateInfo` — stop the sidecar, then start it
/// again with a fresh ephemeral port + session token.
///
/// Idempotent-safe: `stop` blocks until the supervisor has cleaned up and
/// `start` rejects when a worker is already running, so this is the only
/// restart path the UI should use.
#[tauri::command]
pub fn restart(manager: State<'_, WorkerManager>) -> WorkerStateInfo {
    manager.stop();
    if let Err(e) = manager.start() {
        log::error!("worker restart failed: {e}");
    }
    manager.state_info()
}
