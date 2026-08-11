//! Project IPC commands (TASK-008, `MASTER_PLAN.md` §25.1).
//!
//! Thin wrappers over `ProjectService`. Only project-scoped operations are
//! exposed — there is no arbitrary filesystem access — and every id/path is
//! validated in the service before touching disk or DB.

use std::sync::Arc;

use tauri::State;

use crate::db::{DbError, Project};
use crate::services::project_service::ProjectService;

/// `project.create(name, video_path) → Project`
#[tauri::command(rename = "project.create")]
pub fn create(
    service: State<'_, Arc<ProjectService>>,
    name: String,
    video_path: String,
) -> Result<Project, String> {
    service.create(name, video_path).map_err(err_to_string)
}

/// `project.open(id) → Project`
#[tauri::command(rename = "project.open")]
pub fn open(service: State<'_, Arc<ProjectService>>, id: String) -> Result<Project, String> {
    service.load(&id).map_err(err_to_string)
}

/// `project.save(id) → void` (auto-save; records `updated_at`)
#[tauri::command(rename = "project.save")]
pub fn save(service: State<'_, Arc<ProjectService>>, id: String) -> Result<(), String> {
    service.save(&id).map_err(err_to_string)
}

/// `project.delete(id) → void`
#[tauri::command(rename = "project.delete")]
pub fn delete(service: State<'_, Arc<ProjectService>>, id: String) -> Result<(), String> {
    service.delete(&id).map_err(err_to_string)
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
