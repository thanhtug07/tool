//! DictionaryService (TASK-023): project-scoped glossary + character CRUD and
//! the glossary fingerprint used as the translation-memory version.
//!
//! Shares the same SQLite file (`{data_dir}/app.db`) as ProjectService; WAL
//! journal mode allows multiple connections. Every project id is validated as a
//! UUID v4 (the same path-traversal guard as ProjectService), and glossary
//! terms are normalized to lowercase so lookups are case-insensitive.

use std::path::PathBuf;

use crate::db::repo::characters::CharacterRepo;
use crate::db::repo::glossary::GlossaryRepo;
use crate::db::{
    is_valid_uuid_v4, utc_iso8601_now, CharacterEntry, Database, DbError, GlossaryEntry,
};

const MAX_TERM_LEN: usize = 100;
const MAX_NAME_LEN: usize = 100;
const MAX_TEXT_LEN: usize = 1000;

/// The dictionary persistence service, managed as Tauri app state.
pub struct DictionaryService {
    db: Result<Database, DbError>,
}

impl DictionaryService {
    /// Initialize the persistence layer rooted at `data_dir` (same `app.db`).
    pub fn open(data_dir: PathBuf) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("dictionary database init failed: {e}");
        }
        Self { db }
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }

    // ---- glossary ----

    pub fn glossary_list(&self, project_id: &str) -> Result<Vec<GlossaryEntry>, DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        GlossaryRepo::new(&conn).list(&project_id)
    }

    pub fn glossary_upsert(
        &self,
        project_id: &str,
        term: String,
        translation: String,
    ) -> Result<GlossaryEntry, DbError> {
        let project_id = validate_id(project_id)?;
        let term = validate_term(term)?;
        let translation = validate_text(&translation, "translation", MAX_TEXT_LEN)?;
        let entry = GlossaryEntry {
            project_id: project_id.clone(),
            term,
            translation,
            updated_at: utc_iso8601_now(),
        };
        let conn = self.db()?.conn();
        GlossaryRepo::new(&conn).upsert(&entry)?;
        Ok(entry)
    }

    pub fn glossary_delete(&self, project_id: &str, term: &str) -> Result<(), DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        let removed = GlossaryRepo::new(&conn).delete(&project_id, term)?;
        if !removed {
            return Err(DbError::NotFound(format!(
                "glossary term {term:?} does not exist"
            )));
        }
        Ok(())
    }

    /// Glossary version for the TM key: rotates on any glossary edit.
    pub fn glossary_fingerprint(&self, project_id: &str) -> Result<String, DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        GlossaryRepo::new(&conn).fingerprint(&project_id)
    }

    // ---- characters ----

    pub fn character_list(&self, project_id: &str) -> Result<Vec<CharacterEntry>, DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        CharacterRepo::new(&conn).list(&project_id)
    }

    pub fn character_upsert(
        &self,
        project_id: &str,
        name: String,
        description: String,
    ) -> Result<CharacterEntry, DbError> {
        let project_id = validate_id(project_id)?;
        let name = validate_name(name)?;
        let description = validate_text(&description, "description", MAX_TEXT_LEN)?;
        let entry = CharacterEntry {
            project_id: project_id.clone(),
            name,
            description,
            updated_at: utc_iso8601_now(),
        };
        let conn = self.db()?.conn();
        CharacterRepo::new(&conn).upsert(&entry)?;
        Ok(entry)
    }

    pub fn character_delete(&self, project_id: &str, name: &str) -> Result<(), DbError> {
        let project_id = validate_id(project_id)?;
        let conn = self.db()?.conn();
        let removed = CharacterRepo::new(&conn).delete(&project_id, name)?;
        if !removed {
            return Err(DbError::NotFound(format!(
                "character {name:?} does not exist"
            )));
        }
        Ok(())
    }
}

fn validate_id(id: &str) -> Result<String, DbError> {
    if !is_valid_uuid_v4(id) {
        return Err(DbError::InvalidInput(format!("invalid project id: {id:?}")));
    }
    Ok(id.to_string())
}

fn validate_term(term: String) -> Result<String, DbError> {
    let trimmed = term.trim().to_lowercase();
    if trimmed.is_empty() {
        return Err(DbError::InvalidInput(
            "glossary term must not be empty".into(),
        ));
    }
    if trimmed.chars().count() > MAX_TERM_LEN {
        return Err(DbError::InvalidInput(format!(
            "glossary term exceeds {MAX_TERM_LEN} characters"
        )));
    }
    Ok(trimmed)
}

fn validate_name(name: String) -> Result<String, DbError> {
    let trimmed = name.trim().to_string();
    if trimmed.is_empty() {
        return Err(DbError::InvalidInput(
            "character name must not be empty".into(),
        ));
    }
    if trimmed.chars().count() > MAX_NAME_LEN {
        return Err(DbError::InvalidInput(format!(
            "character name exceeds {MAX_NAME_LEN} characters"
        )));
    }
    Ok(trimmed)
}

fn validate_text(value: &str, label: &str, max: usize) -> Result<String, DbError> {
    let trimmed = value.trim().to_string();
    if trimmed.chars().count() > max {
        return Err(DbError::InvalidInput(format!(
            "{label} exceeds {max} characters"
        )));
    }
    Ok(trimmed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::new_uuid_v4;

    fn service(label: &str) -> (DictionaryService, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_dict_svc_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let svc = DictionaryService::open(dir.clone());
        assert!(svc.db().is_ok(), "db must open cleanly for tests");
        (svc, dir)
    }

    fn seed_project(svc: &DictionaryService) -> String {
        let pid = new_uuid_v4().unwrap();
        let conn = svc.db().expect("db").conn();
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, 'seed', 'v.mp4', 'draft', 't0', 't0')",
            rusqlite::params![pid],
        )
        .expect("seed project");
        pid
    }

    #[test]
    fn glossary_crud_roundtrip_with_case_normalization() {
        let (svc, dir) = service("glossary");
        let pid = seed_project(&svc);
        let entry = svc
            .glossary_upsert(&pid, "API".into(), "Giao diện".into())
            .expect("upsert");
        assert_eq!(entry.term, "api", "terms are lowercased");
        let list = svc.glossary_list(&pid).expect("list");
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].translation, "Giao diện");
        assert!(matches!(svc.glossary_delete(&pid, "api"), Ok(())));
        assert!(svc.glossary_list(&pid).expect("list").is_empty());
        assert!(matches!(
            svc.glossary_delete(&pid, "api"),
            Err(DbError::NotFound(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn glossary_fingerprint_rotates_on_change() {
        let (svc, dir) = service("fp");
        let pid = seed_project(&svc);
        let v1 = svc.glossary_fingerprint(&pid).expect("v1");
        svc.glossary_upsert(&pid, "API".into(), "Giao diện".into())
            .expect("upsert");
        let v2 = svc.glossary_fingerprint(&pid).expect("v2");
        assert_ne!(v1, v2);
        svc.glossary_upsert(&pid, "API".into(), "Khác".into())
            .expect("edit");
        let v3 = svc.glossary_fingerprint(&pid).expect("v3");
        assert_ne!(v2, v3);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn character_crud_roundtrip() {
        let (svc, dir) = service("chars");
        let pid = seed_project(&svc);
        svc.character_upsert(&pid, "Nam".into(), "nhân vật chính".into())
            .expect("upsert");
        let list = svc.character_list(&pid).expect("list");
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].name, "Nam");
        assert!(svc.character_delete(&pid, "Nam").is_ok());
        assert!(matches!(
            svc.character_delete(&pid, "Nam"),
            Err(DbError::NotFound(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn invalid_inputs_are_rejected() {
        let (svc, dir) = service("invalid");
        let pid = seed_project(&svc);
        assert!(matches!(
            svc.glossary_upsert("not-a-uuid", "x".into(), "y".into()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.glossary_upsert(&pid, "  ".into(), "y".into()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.character_upsert(&pid, String::new(), "d".into()),
            Err(DbError::InvalidInput(_))
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
