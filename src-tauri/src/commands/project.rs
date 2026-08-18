//! Project IPC commands (TASK-008, `MASTER_PLAN.md` §25.1).
//!
//! Thin wrappers over `ProjectService`. Only project-scoped operations are
//! exposed — there is no arbitrary filesystem access — and every id/path is
//! validated in the service before touching disk or DB.

use std::sync::Arc;

use tauri::{AppHandle, State};

use crate::commands::media::allow_project_media;
use crate::db::{DbError, Project};
use crate::services::project_service::ProjectService;

/// `project.create(name, video_path) → Project`
///
/// The new project's source video + working directory are added to the
/// asset-protocol scope immediately, so the video preview works right after
/// selection without an app restart.
#[tauri::command(rename = "project.create")]
pub fn create(
    app: AppHandle,
    service: State<'_, Arc<ProjectService>>,
    name: String,
    video_path: String,
) -> Result<Project, String> {
    let project = service.create(name, video_path).map_err(err_to_string)?;
    allow_project_media(&app, &project, &service.project_dir(&project.id));
    Ok(project)
}

/// `project.open(id) → Project`
#[tauri::command(rename = "project.open")]
pub fn open(service: State<'_, Arc<ProjectService>>, id: String) -> Result<Project, String> {
    service.load(&id).map_err(err_to_string)
}

/// `project.findBySourceVideo(video_path) → Project | null`
///
/// Reopens a project that already uses the given source video (case- and
/// separator-insensitive) so importing the same file never creates a
/// duplicate project.
#[tauri::command(rename = "project.findBySourceVideo")]
pub fn find_by_source_video(
    service: State<'_, Arc<ProjectService>>,
    video_path: String,
) -> Result<Option<Project>, String> {
    service
        .find_by_source_video_path(&video_path)
        .map_err(err_to_string)
}

/// `project.list() → Project[]` (most recently updated first)
#[tauri::command(rename = "project.list")]
pub fn list_projects(service: State<'_, Arc<ProjectService>>) -> Result<Vec<Project>, String> {
    service.list().map_err(err_to_string)
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

/// `project.rename(id, new_name) → Project`
#[tauri::command(rename = "project.rename")]
pub fn rename(
    service: State<'_, Arc<ProjectService>>,
    id: String,
    new_name: String,
) -> Result<Project, String> {
    service.rename(&id, new_name).map_err(err_to_string)
}

/// `project.updateSettings(id, settings_json) → Project` (project-level
/// overrides; pass `null` to clear).
#[tauri::command(rename = "project.updateSettings")]
pub fn update_settings(
    service: State<'_, Arc<ProjectService>>,
    id: String,
    settings_json: Option<String>,
) -> Result<Project, String> {
    service
        .update_settings(&id, settings_json)
        .map_err(err_to_string)
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
