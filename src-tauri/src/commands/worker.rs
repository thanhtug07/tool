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
