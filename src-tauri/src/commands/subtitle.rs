//! Subtitle editor IPC commands (TASK-025, `MASTER_PLAN.md` §25.2).
//!
//! Thin wrappers over `SubtitleService`: listing cues for the editor, atomic
//! replace of a project's cue set (worker SubtitleEngine import), and in-place
//! editor saves via ``subtitle.update_cue``.

use std::sync::Arc;

use tauri::State;

use crate::db::{DbError, SubtitleCue};
use crate::services::subtitle_service::{CueInput, CuePatch, SubtitleService};

/// `subtitle.get_cues(project_id) → SubtitleCue[]`
#[tauri::command(rename = "subtitle.get_cues")]
pub fn get_cues(
    service: State<'_, Arc<SubtitleService>>,
    project_id: String,
) -> Result<Vec<SubtitleCue>, String> {
    service.list(&project_id).map_err(err_to_string)
}

/// `subtitle.replace_cues(project_id, cues) → number of cues saved`
#[tauri::command(rename = "subtitle.replace_cues")]
pub fn replace_cues(
    service: State<'_, Arc<SubtitleService>>,
    project_id: String,
    cues: Vec<CueInput>,
) -> Result<usize, String> {
    service
        .replace_project(&project_id, cues)
        .map_err(err_to_string)
}

/// `subtitle.update_cue(id, patch) → SubtitleCue`
#[tauri::command(rename = "subtitle.update_cue")]
pub fn update_cue(
    service: State<'_, Arc<SubtitleService>>,
    id: String,
    patch: CuePatch,
) -> Result<SubtitleCue, String> {
    service.update_cue(&id, patch).map_err(err_to_string)
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
