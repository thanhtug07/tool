//! System-level IPC commands (TASK-004).
//!
//! The first command proving the typed frontend ↔ Rust bridge works end to end.

/// Returns `"pong"` when the Rust core is reachable.
///
/// Deterministic and side-effect free by design; used as a connectivity probe
/// by the frontend "Test connection" control (Settings > About).
#[tauri::command]
pub fn ping() -> Result<String, String> {
    Ok("pong".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ping_returns_pong() {
        assert_eq!(ping(), Ok("pong".to_string()));
    }
}
