//! Pipeline IPC commands (RELEASE-P0-005).
//!
//! Exposes read-only pipeline metadata the frontend needs to drive the real
//! workflow: the canonical per-project artifact paths (audio, transcript,
//! translation, subtitles, rendered video) that the runner writes and the
//! editor/preview/export surfaces consume. No job execution lives here — that
//! goes through `job.submit` + the PipelineRunner.

use std::sync::Arc;

use serde::Serialize;
use tauri::State;

use crate::db::{DbError, Job};
use crate::services::job_service::JobService;
use crate::services::pipeline_runner::artifact_paths;
use crate::services::project_service::ProjectService;

/// Serialized artifact paths for one project (wire shape).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArtifactPathsPayload {
    pub project_dir: String,
    pub audio: String,
    pub transcript: String,
    pub translation: String,
    pub subtitle_srt: String,
    pub subtitle_ass: String,
    pub rendered_video: String,
}

/// `pipeline.artifact_paths(project_id) → ArtifactPaths` (read-only)
#[tauri::command(rename = "pipeline.artifact_paths")]
pub fn artifact_paths_command(
    service: State<'_, Arc<ProjectService>>,
    project_id: String,
) -> Result<ArtifactPathsPayload, String> {
    // Validate the project exists (also a UUID v4 / path-traversal guard).
    service.load(&project_id).map_err(err_to_string)?;
    let paths = artifact_paths(&service.project_dir(&project_id));
    Ok(ArtifactPathsPayload {
        project_dir: paths.project_dir.display().to_string(),
        audio: paths.audio.display().to_string(),
        transcript: paths.transcript.display().to_string(),
        translation: paths.translation.display().to_string(),
        subtitle_srt: paths.subtitle_srt.display().to_string(),
        subtitle_ass: paths.subtitle_ass.display().to_string(),
        rendered_video: paths.rendered_video.display().to_string(),
    })
}

/// `pipeline.submit(project_id, params) → Job` (orchestrator v2, Rust owns DAG)
#[tauri::command(rename = "pipeline.submit")]
pub fn pipeline_submit(
    job_service: State<'_, JobService>,
    project_id: String,
    params: serde_json::Value,
) -> Result<Job, String> {
    job_service
        .submit_pipeline(&project_id, params)
        .map_err(|e| e.to_string())
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
