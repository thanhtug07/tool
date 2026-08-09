//! Library entry for the Tauri Rust core.
//! TASK-001: foundation scaffold. TASK-004: first typed IPC command (`system::ping`).

pub mod commands;

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![commands::system::ping])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
