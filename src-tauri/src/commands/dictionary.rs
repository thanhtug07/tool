//! Dictionary IPC commands (TASK-023, `MASTER_PLAN.md` §25.1).
//!
//! Thin wrappers over `DictionaryService`: project-scoped glossary and
//! character CRUD plus the glossary fingerprint (translation-memory version).
//! Project ids are validated in the service (UUID v4 only) before any DB work.

use std::sync::Arc;

use tauri::State;

use crate::db::{CharacterEntry, DbError, GlossaryEntry};
use crate::services::dictionary_service::DictionaryService;

/// `dictionary.glossary.list(project_id) → GlossaryEntry[]`
#[tauri::command(rename = "dictionary.glossary.list")]
pub fn glossary_list(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
) -> Result<Vec<GlossaryEntry>, String> {
    service.glossary_list(&project_id).map_err(err_to_string)
}

/// `dictionary.glossary.upsert(project_id, term, translation) → GlossaryEntry`
#[tauri::command(rename = "dictionary.glossary.upsert")]
pub fn glossary_upsert(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
    term: String,
    translation: String,
) -> Result<GlossaryEntry, String> {
    service
        .glossary_upsert(&project_id, term, translation)
        .map_err(err_to_string)
}

/// `dictionary.glossary.delete(project_id, term) → void`
#[tauri::command(rename = "dictionary.glossary.delete")]
pub fn glossary_delete(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
    term: String,
) -> Result<(), String> {
    service
        .glossary_delete(&project_id, &term)
        .map_err(err_to_string)
}

/// `dictionary.glossary.fingerprint(project_id) → String`
#[tauri::command(rename = "dictionary.glossary.fingerprint")]
pub fn glossary_fingerprint(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
) -> Result<String, String> {
    service
        .glossary_fingerprint(&project_id)
        .map_err(err_to_string)
}

/// `dictionary.character.list(project_id) → CharacterEntry[]`
#[tauri::command(rename = "dictionary.character.list")]
pub fn character_list(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
) -> Result<Vec<CharacterEntry>, String> {
    service.character_list(&project_id).map_err(err_to_string)
}

/// `dictionary.character.upsert(project_id, name, description) → CharacterEntry`
#[tauri::command(rename = "dictionary.character.upsert")]
pub fn character_upsert(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
    name: String,
    description: String,
) -> Result<CharacterEntry, String> {
    service
        .character_upsert(&project_id, name, description)
        .map_err(err_to_string)
}

/// `dictionary.character.delete(project_id, name) → void`
#[tauri::command(rename = "dictionary.character.delete")]
pub fn character_delete(
    service: State<'_, Arc<DictionaryService>>,
    project_id: String,
    name: String,
) -> Result<(), String> {
    service
        .character_delete(&project_id, &name)
        .map_err(err_to_string)
}

fn err_to_string(e: DbError) -> String {
    e.to_string()
}
