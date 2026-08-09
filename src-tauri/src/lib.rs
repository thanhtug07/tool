//! Library entry for the Tauri Rust core.
//! Foundation scaffold (TASK-001). Commands/services modules come in later tasks.

pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
