use tauri::State;

use crate::db::repo::task::Task;
use crate::services::job_service::JobService;

#[tauri::command(rename = "task.list")]
pub fn task_list(service: State<'_, JobService>, job_id: String) -> Result<Vec<Task>, String> {
    service.list_tasks(&job_id).map_err(|e| e.to_string())
}

#[tauri::command(rename = "task.get")]
pub fn task_get(service: State<'_, JobService>, id: String) -> Result<Task, String> {
    service.get_task(&id).map_err(|e| e.to_string())
}
