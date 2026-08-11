//! Export IPC commands (TASK-029, `MASTER_PLAN.md` §25.2).
//!
//! Thin proxies to the worker's export endpoints. The Rust core holds the
//! per-session bearer token (never serialized, never logged) and forwards the
//! user's source path / target directory; the worker performs the copy + QC
//! against the shared local filesystem and answers with the final path and a
//! QC report (or a canonical error envelope that is surfaced verbatim).
//!
//! Security model: no arbitrary command execution here — paths are forwarded
//! as data, and the worker validates existence/writability itself. Errors are
//! user-facing strings (architecture error codes), never stack traces.

use tauri::State;

use crate::services::worker_client::{
    ExportSubtitleRequest, ExportSubtitleResponse, ExportVideoRequest, ExportVideoResponse,
};
use crate::services::worker_manager::WorkerManager;

/// `export.video(sourceVideo, targetDir, name?, runQc?) → ExportVideoResponse`
#[tauri::command(rename = "export.video")]
pub fn export_video(
    manager: State<'_, WorkerManager>,
    source_video: String,
    target_dir: String,
    name: Option<String>,
    run_qc: Option<bool>,
) -> Result<ExportVideoResponse, String> {
    let client = manager
        .worker_client()
        .ok_or_else(|| "The worker is not running.".to_string())?;
    client
        .export_video(ExportVideoRequest {
            source_video,
            target_dir,
            name,
            run_qc,
        })
        .map_err(|e| e.to_string())
}

/// `export.subtitles(sourceSubtitle, targetDir, name?, format?) → path`
#[tauri::command(rename = "export.subtitles")]
pub fn export_subtitles(
    manager: State<'_, WorkerManager>,
    source_subtitle: String,
    target_dir: String,
    name: Option<String>,
    format: Option<String>,
) -> Result<ExportSubtitleResponse, String> {
    let client = manager
        .worker_client()
        .ok_or_else(|| "The worker is not running.".to_string())?;
    client
        .export_subtitles(ExportSubtitleRequest {
            source_subtitle,
            target_dir,
            name,
            format,
        })
        .map_err(|e| e.to_string())
}
