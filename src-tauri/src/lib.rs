//! Library entry for the Tauri Rust core.
//! Foundation scaffold (TASK-001). Commands/services modules come in later tasks.

pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
