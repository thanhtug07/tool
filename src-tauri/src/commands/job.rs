//! Job IPC commands (TASK-010, `MASTER_PLAN.md` §25.1).
//!
//! Thin wrappers over `JobService`. Job ids and project ids are validated in
//! the service before touching the DB; only project-scoped operations are
//! exposed (no arbitrary id access).

use tauri::State;

use crate::db::{DbError, Job, JobType};
use crate::services::job_service::JobService;

/// `job.submit(project_id, type, params) → Job`
#[tauri::command(rename = "job.submit")]
pub fn submit(
    service: State<'_, JobService>,
    project_id: String,
    job_type: JobType,
    params: serde_json::Value,
) -> Result<Job, String> {
    service
        .submit(&project_id, job_type, params)
        .map_err(err_to_string)
}

/// `job.get(id) → Job`
#[tauri::command(rename = "job.get")]
pub fn get(service: State<'_, JobService>, id: String) -> Result<Job, String> {
    service.get(&id).map_err(err_to_string)
}

/// `job.list(project_id) → Job[]` (newest updated first)
#[tauri::command(rename = "job.list")]
pub fn list(service: State<'_, JobService>, project_id: String) -> Result<Vec<Job>, String> {
    service.list(&project_id).map_err(err_to_string)
}

/// `job.list_all(limit?) → Job[]` across all projects (newest updated first).
#[tauri::command(rename = "job.list_all")]
pub fn list_all(service: State<'_, JobService>, limit: Option<u32>) -> Result<Vec<Job>, String> {
    service
        .list_recent(limit.unwrap_or(200))
        .map_err(err_to_string)
}

/// `job.cancel(id) → void`
#[tauri::command(rename = "job.cancel")]
pub fn cancel(service: State<'_, JobService>, id: String) -> Result<(), String> {
    service.cancel(&id).map_err(err_to_string)
}

/// `job.retry(id) → void`
#[tauri::command(rename = "job.retry")]
pub fn retry(service: State<'_, JobService>, id: String) -> Result<(), String> {
    service.retry(&id).map_err(err_to_string)
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
