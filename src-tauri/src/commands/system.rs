//! System-level IPC commands (TASK-004).
//!
//! The first command proving the typed frontend ↔ Rust bridge works end to end.

use std::sync::Arc;

use tauri::State;

use crate::services::hardware_probe::{HardwareProfile, SystemInfo};

/// Returns `"pong"` when the Rust core is reachable.
///
/// Deterministic and side-effect free by design; used as a connectivity probe
/// by the frontend "Test connection" control (Settings > About).
#[tauri::command]
pub fn ping() -> Result<String, String> {
    Ok("pong".to_string())
}

/// Returns the cached machine hardware snapshot (GPU, RAM, FFmpeg encoders).
///
/// The probe runs once (lazily) and is reused for every caller; the payload is
/// static hardware info — never live usage %. Live CPU/GPU/RAM usage is not
/// exposed by any backend endpoint, so the UI must not fake it.
#[tauri::command(rename = "system.hardware")]
pub fn hardware(info: State<'_, Arc<SystemInfo>>) -> HardwareProfile {
    info.get()
}

/// Reveal a file or folder in the OS file manager (Automation live-log
/// "Open Output" / "Open Folder" buttons).
///
/// Windows opens an Explorer window with the target selected
/// (`explorer /select,<path>`); other platforms are not supported yet and
/// return a clear error instead of silently doing nothing.
#[tauri::command(rename = "system.reveal")]
pub fn reveal(path: String) -> Result<(), String> {
    if path.trim().is_empty() {
        return Err("cannot reveal an empty path".to_string());
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg("/select,")
            .arg(&path)
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("failed to open Explorer for {path}: {e}"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = path;
        Err("reveal is only implemented on Windows".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ping_returns_pong() {
        assert_eq!(ping(), Ok("pong".to_string()));
    }
}
