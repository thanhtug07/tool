//! Character dictionary repository (TASK-023).
//!
//! Project-scoped rows (`PRIMARY KEY (project_id, name)`) holding a display
//! name and a one-line description per character, used to build the character
//! context section of translation prompts (see `worker/src/services/
//! context_service.py`).

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::db::DbError;

/// One character dictionary row (wire + DB representation).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CharacterEntry {
    pub project_id: String,
    pub name: String,
    pub description: String,
    /// ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
    pub updated_at: String,
}

pub struct CharacterRepo<'a> {
    conn: &'a Connection,
}

const COLUMNS: &str = "project_id, name, description, updated_at";

impl<'a> CharacterRepo<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    /// All characters of a project, ordered by name.
    pub fn list(&self, project_id: &str) -> Result<Vec<CharacterEntry>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM character_entries WHERE project_id = ?1 ORDER BY name"
        ))?;
        let rows = stmt.query_map(params![project_id], row_to_entry)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Insert or update a character (upsert on `(project_id, name)`).
    pub fn upsert(&self, entry: &CharacterEntry) -> Result<(), DbError> {
        self.conn.execute(
            "INSERT INTO character_entries (project_id, name, description, updated_at)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(project_id, name) DO UPDATE SET
                 description = excluded.description,
                 updated_at = excluded.updated_at",
            params![
                entry.project_id,
                entry.name,
                entry.description,
                entry.updated_at,
            ],
        )?;
        Ok(())
    }

    /// Delete a character. Returns `false` when no row matched.
    pub fn delete(&self, project_id: &str, name: &str) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "DELETE FROM character_entries WHERE project_id = ?1 AND name = ?2",
            params![project_id, name],
        )?;
        Ok(n > 0)
    }
}

fn row_to_entry(row: &rusqlite::Row<'_>) -> rusqlite::Result<CharacterEntry> {
    Ok(CharacterEntry {
        project_id: row.get(0)?,
        name: row.get(1)?,
        description: row.get(2)?,
        updated_at: row.get(3)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{new_uuid_v4, utc_iso8601_now, Database};

    fn repo(label: &str) -> (Database, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_characters_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Database::open(&dir.join("app.db")).expect("open");
        (db, dir)
    }

    fn entry(project_id: &str, name: &str, description: &str) -> CharacterEntry {
        CharacterEntry {
            project_id: project_id.into(),
            name: name.into(),
            description: description.into(),
            updated_at: utc_iso8601_now(),
        }
    }

    fn seed_project(conn: &Connection, project_id: &str) {
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, 'seed', 'v.mp4', 'draft', 't0', 't0')",
            params![project_id],
        )
        .expect("seed project");
    }

    #[test]
    fn upsert_list_roundtrip() {
        let (db, dir) = repo("list");
        let conn = db.conn();
        let repo = CharacterRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "Nam", "nhân vật chính"))
            .expect("a");
        repo.upsert(&entry(&pid, "Lan", "bạn thân")).expect("b");
        let list = repo.list(&pid).expect("list");
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].name, "Lan");
        assert_eq!(list[1].name, "Nam");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn upsert_updates_description_in_place() {
        let (db, dir) = repo("update");
        let conn = db.conn();
        let repo = CharacterRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "Nam", "cũ")).expect("first");
        repo.upsert(&entry(&pid, "Nam", "mới")).expect("second");
        let list = repo.list(&pid).expect("list");
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].description, "mới");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn delete_removes_and_reports_missing() {
        let (db, dir) = repo("delete");
        let conn = db.conn();
        let repo = CharacterRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "Nam", "x")).expect("upsert");
        assert!(repo.delete(&pid, "Nam").expect("delete"));
        assert!(!repo.delete(&pid, "Nam").expect("delete again"));
        assert!(repo.list(&pid).expect("list").is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
