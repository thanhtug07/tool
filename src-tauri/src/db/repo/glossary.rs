//! Glossary repository (TASK-023).
//!
//! Rows are project-scoped (`PRIMARY KEY (project_id, term)`). The service
//! normalizes terms to lowercase before insert, so lookups are case-insensitive
//! by construction. ``fingerprint`` produces a stable project glossary version
//! (FNV-1a 64-bit hex over sorted ``term\0translation`` rows) used as the
//! `glossary_ver` component of the translation-memory key — when the glossary
//! changes, the fingerprint changes and stale TM entries become misses.

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::db::DbError;

/// One glossary term mapping (wire + DB representation).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GlossaryEntry {
    pub project_id: String,
    /// Canonical term (lowercased by the service layer).
    pub term: String,
    pub translation: String,
    /// ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
    pub updated_at: String,
}

pub struct GlossaryRepo<'a> {
    conn: &'a Connection,
}

const COLUMNS: &str = "project_id, term, translation, updated_at";

impl<'a> GlossaryRepo<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    /// All glossary entries of a project, ordered by term.
    pub fn list(&self, project_id: &str) -> Result<Vec<GlossaryEntry>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM glossary_entries WHERE project_id = ?1 ORDER BY term"
        ))?;
        let rows = stmt.query_map(params![project_id], row_to_entry)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Insert or update a glossary entry (upsert on `(project_id, term)`).
    pub fn upsert(&self, entry: &GlossaryEntry) -> Result<(), DbError> {
        self.conn.execute(
            "INSERT INTO glossary_entries (project_id, term, translation, updated_at)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(project_id, term) DO UPDATE SET
                 translation = excluded.translation,
                 updated_at = excluded.updated_at",
            params![
                entry.project_id,
                entry.term,
                entry.translation,
                entry.updated_at,
            ],
        )?;
        Ok(())
    }

    /// Delete a glossary entry. Returns `false` when no row matched.
    pub fn delete(&self, project_id: &str, term: &str) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "DELETE FROM glossary_entries WHERE project_id = ?1 AND term = ?2",
            params![project_id, term],
        )?;
        Ok(n > 0)
    }

    /// Stable glossary version: FNV-1a 64-bit hex over sorted term rows.
    ///
    /// Any change to the term set or a translation rotates the value, which
    /// invalidates translation-memory entries for this project.
    pub fn fingerprint(&self, project_id: &str) -> Result<String, DbError> {
        let mut rows: Vec<(String, String)> = Vec::new();
        {
            let mut stmt = self.conn.prepare(
                "SELECT term, translation FROM glossary_entries WHERE project_id = ?1 ORDER BY term",
            )?;
            let mut rows_iter = stmt.query(params![project_id])?;
            while let Some(row) = rows_iter.next()? {
                rows.push((row.get(0)?, row.get(1)?));
            }
        }
        let mut hash: u64 = 0xcbf29ce484222325;
        for (term, translation) in &rows {
            for byte in term.as_bytes() {
                hash = fnv1a_step(hash, *byte);
            }
            hash = fnv1a_step(hash, 0);
            for byte in translation.as_bytes() {
                hash = fnv1a_step(hash, *byte);
            }
            hash = fnv1a_step(hash, 0xff);
        }
        Ok(format!("{hash:016x}"))
    }
}

fn fnv1a_step(hash: u64, byte: u8) -> u64 {
    (hash ^ u64::from(byte)).wrapping_mul(0x100000001b3)
}

fn row_to_entry(row: &rusqlite::Row<'_>) -> rusqlite::Result<GlossaryEntry> {
    Ok(GlossaryEntry {
        project_id: row.get(0)?,
        term: row.get(1)?,
        translation: row.get(2)?,
        updated_at: row.get(3)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{new_uuid_v4, utc_iso8601_now, Database};

    fn repo(label: &str) -> (Database, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_glossary_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Database::open(&dir.join("app.db")).expect("open");
        (db, dir)
    }

    fn entry(project_id: &str, term: &str, translation: &str) -> GlossaryEntry {
        GlossaryEntry {
            project_id: project_id.into(),
            term: term.into(),
            translation: translation.into(),
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
        let repo = GlossaryRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "api", "giao diện"))
            .expect("upsert");
        repo.upsert(&entry(&pid, "render", "xuất")).expect("upsert");
        let list = repo.list(&pid).expect("list");
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].term, "api");
        assert_eq!(list[1].term, "render");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn upsert_is_idempotent_on_same_term() {
        let (db, dir) = repo("upsert");
        let conn = db.conn();
        let repo = GlossaryRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "api", "một")).expect("first");
        repo.upsert(&entry(&pid, "api", "hai")).expect("second");
        let list = repo.list(&pid).expect("list");
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].translation, "hai");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn delete_removes_term_and_reports_missing() {
        let (db, dir) = repo("delete");
        let conn = db.conn();
        let repo = GlossaryRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.upsert(&entry(&pid, "api", "x")).expect("upsert");
        assert!(repo.delete(&pid, "api").expect("delete"));
        assert!(!repo.delete(&pid, "api").expect("delete again"));
        assert!(repo.list(&pid).expect("list").is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn fingerprint_is_scoped_stable_and_changes_on_edit() {
        let (db, dir) = repo("fp");
        let conn = db.conn();
        let repo = GlossaryRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        let other = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        seed_project(&conn, &other);
        repo.upsert(&entry(&pid, "api", "giao diện")).expect("a1");
        let v1 = repo.fingerprint(&pid).expect("v1");
        let v1_again = repo.fingerprint(&pid).expect("v1 again");
        assert_eq!(v1, v1_again, "no change -> same version");
        assert_eq!(
            repo.fingerprint(&other).expect("other"),
            "cbf29ce484222325",
            "empty glossary = stable FNV offset basis"
        );

        repo.upsert(&entry(&pid, "render", "xuất")).expect("a2");
        let v2 = repo.fingerprint(&pid).expect("v2");
        assert_ne!(v1, v2, "adding a term rotates the version");

        repo.upsert(&entry(&pid, "api", "giao diện lập trình"))
            .expect("edit");
        let v3 = repo.fingerprint(&pid).expect("v3");
        assert_ne!(v2, v3, "editing a translation rotates the version");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
