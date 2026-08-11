//! Subtitle cues repository (TASK-025, `MASTER_PLAN.md` §17.1).
//!
//! The parsed subtitle engine output lives one row per cue, ordered by
//! ``cue_number`` (unique per project). The editor reads the rows, edits text
//! and timing in place via ``update``, and during the pipeline the worker's
//! SubtitleEngine output replaces the whole set atomically (``delete_project``
//! + ``insert_many`` inside the service's transaction).

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

use crate::db::DbError;

/// Wire + DB representation of one subtitle cue (editor display has timing,
/// speaker, source text, target text and status).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SubtitleCue {
    pub id: String,
    pub project_id: String,
    /// 1-based display order, unique per project.
    pub cue_number: i64,
    /// Start time in seconds.
    pub start: f64,
    /// End time in seconds.
    pub end: f64,
    /// Target subtitle text (edited by the user).
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speaker: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_text: Option<String>,
    /// draft/translated/edited/approved.
    #[serde(default = "default_status")]
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub style_json: Option<String>,
    /// ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
    pub updated_at: String,
}

fn default_status() -> String {
    "draft".to_string()
}

pub struct SubtitleRepo<'a> {
    conn: &'a Connection,
}

const COLUMNS: &str = "id, project_id, cue_number, start, end, text, speaker, source_text, status, style_json, updated_at";

impl<'a> SubtitleRepo<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    /// All cues of a project, ordered by cue number (1-based display order).
    pub fn list(&self, project_id: &str) -> Result<Vec<SubtitleCue>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM subtitle_cues WHERE project_id = ?1 ORDER BY cue_number"
        ))?;
        let rows = stmt.query_map(params![project_id], row_to_cue)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Insert rows (caller owns the transaction; used by the pipeline import).
    pub fn insert_many(&self, cues: &[SubtitleCue]) -> Result<(), DbError> {
        for cue in cues {
            let style_json: Option<&str> = cue.style_json.as_deref();
            let speaker: Option<&str> = cue.speaker.as_deref();
            let source_text: Option<&str> = cue.source_text.as_deref();
            self.conn.execute(
                "INSERT INTO subtitle_cues (id, project_id, cue_number, start, end, text, speaker, source_text, status, style_json, updated_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    cue.id,
                    cue.project_id,
                    cue.cue_number,
                    cue.start,
                    cue.end,
                    cue.text,
                    speaker,
                    source_text,
                    cue.status,
                    style_json,
                    cue.updated_at,
                ],
            )?;
        }
        Ok(())
    }

    /// Remove every cue of a project (used by the atomic pipeline replace).
    pub fn delete_project(&self, project_id: &str) -> Result<(), DbError> {
        self.conn.execute(
            "DELETE FROM subtitle_cues WHERE project_id = ?1",
            params![project_id],
        )?;
        Ok(())
    }

    /// Fetch one cue by id.
    pub fn get(&self, id: &str) -> Result<Option<SubtitleCue>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM subtitle_cues WHERE id = ?1"
        ))?;
        let mut rows = stmt.query(params![id])?;
        match rows.next()? {
            Some(row) => Ok(Some(row_to_cue(row)?)),
            None => Ok(None),
        }
    }

    /// Overwrite the user-editable fields of one cue. Returns the updated row,
    /// or `None` when no cue with that id exists.
    #[allow(clippy::too_many_arguments)]
    pub fn update(
        &self,
        id: &str,
        start: f64,
        end: f64,
        text: &str,
        speaker: Option<&str>,
        status: &str,
        updated_at: &str,
    ) -> Result<Option<SubtitleCue>, DbError> {
        let n = self.conn.execute(
            "UPDATE subtitle_cues
             SET start = ?2, end = ?3, text = ?4, speaker = ?5, status = ?6, updated_at = ?7
             WHERE id = ?1",
            params![id, start, end, text, speaker, status, updated_at],
        )?;
        if n == 0 {
            return Ok(None);
        }
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM subtitle_cues WHERE id = ?1"
        ))?;
        let row = stmt.query_row(params![id], row_to_cue)?;
        Ok(Some(row))
    }
}

fn row_to_cue(row: &rusqlite::Row<'_>) -> rusqlite::Result<SubtitleCue> {
    Ok(SubtitleCue {
        id: row.get(0)?,
        project_id: row.get(1)?,
        cue_number: row.get(2)?,
        start: row.get(3)?,
        end: row.get(4)?,
        text: row.get(5)?,
        speaker: row.get(6)?,
        source_text: row.get(7)?,
        status: row.get(8)?,
        style_json: row.get(9)?,
        updated_at: row.get(10)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{new_uuid_v4, utc_iso8601_now, Database};

    fn repo(label: &str) -> (Database, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_subtitle_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Database::open(&dir.join("app.db")).expect("open");
        (db, dir)
    }

    fn seed_project(conn: &Connection, project_id: &str) {
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, 'seed', 'v.mp4', 'draft', 't0', 't0')",
            params![project_id],
        )
        .expect("seed project");
    }

    fn cue(project_id: &str, number: i64, text: &str) -> SubtitleCue {
        SubtitleCue {
            id: new_uuid_v4().expect("uuid"),
            project_id: project_id.into(),
            cue_number: number,
            start: number as f64,
            end: number as f64 + 1.0,
            text: text.into(),
            speaker: None,
            source_text: None,
            status: "draft".into(),
            style_json: None,
            updated_at: utc_iso8601_now(),
        }
    }

    #[test]
    fn list_is_ordered_by_cue_number() {
        let (db, dir) = repo("list");
        let conn = db.conn();
        let repo = SubtitleRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        let c1 = cue(&pid, 2, "hai");
        let c2 = cue(&pid, 1, "một");
        repo.insert_many(&[c1, c2]).expect("insert");
        let list = repo.list(&pid).expect("list");
        assert_eq!(list.len(), 2);
        assert_eq!(list[0].cue_number, 1);
        assert_eq!(list[1].cue_number, 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_overwrites_editable_fields() {
        let (db, dir) = repo("update");
        let conn = db.conn();
        let repo = SubtitleRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        let cue = cue(&pid, 1, "gốc");
        repo.insert_many(std::slice::from_ref(&cue))
            .expect("insert");

        let updated = repo
            .update(&cue.id, 2.0, 4.0, "sửa rồi", Some("A"), "edited", "t1")
            .expect("update")
            .expect("row exists");
        assert_eq!(updated.start, 2.0);
        assert_eq!(updated.end, 4.0);
        assert_eq!(updated.text, "sửa rồi");
        assert_eq!(updated.speaker.as_deref(), Some("A"));
        assert_eq!(updated.status, "edited");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_missing_id_returns_none() {
        let (db, dir) = repo("missing");
        let conn = db.conn();
        let repo = SubtitleRepo::new(&conn);
        let none = repo
            .update(
                "00000000-0000-4000-8000-000000000000",
                0.0,
                1.0,
                "x",
                None,
                "draft",
                "t",
            )
            .expect("no error");
        assert!(none.is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn delete_project_clears_all_cues() {
        let (db, dir) = repo("delete");
        let conn = db.conn();
        let repo = SubtitleRepo::new(&conn);
        let pid = new_uuid_v4().unwrap();
        seed_project(&conn, &pid);
        repo.insert_many(&[cue(&pid, 1, "a"), cue(&pid, 2, "b")])
            .expect("insert");
        repo.delete_project(&pid).expect("delete");
        assert!(repo.list(&pid).expect("list").is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
