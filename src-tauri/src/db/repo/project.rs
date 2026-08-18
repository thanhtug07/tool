//! Project repository (TASK-008): full CRUD for the `projects` table.
//!
//! The row shape is canonical: `schemas/project.schema.json` (TASK-007
//! single-source-of-truth) mirrors `MASTER_PLAN.md` §17.1 `projects` table and
//! is shared with the TypeScript types and the generated Pydantic models.

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

use crate::db::DbError;

/// Lifecycle stage of a project (`MASTER_PLAN.md` §17.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ProjectStatus {
    #[default]
    Draft,
    Analyzed,
    Transcribed,
    Translated,
    Rendered,
}

impl ProjectStatus {
    /// Canonical lowercase DB representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            ProjectStatus::Draft => "draft",
            ProjectStatus::Analyzed => "analyzed",
            ProjectStatus::Transcribed => "transcribed",
            ProjectStatus::Translated => "translated",
            ProjectStatus::Rendered => "rendered",
        }
    }

    pub fn from_db_str(s: &str) -> Option<ProjectStatus> {
        match s {
            "draft" => Some(ProjectStatus::Draft),
            "analyzed" => Some(ProjectStatus::Analyzed),
            "transcribed" => Some(ProjectStatus::Transcribed),
            "translated" => Some(ProjectStatus::Translated),
            "rendered" => Some(ProjectStatus::Rendered),
            _ => None,
        }
    }
}

/// A video localization project (wire + DB representation).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Project {
    /// UUID v4 (validated at the service/IPC boundary).
    pub id: String,
    pub name: String,
    pub source_video_path: String,
    pub status: ProjectStatus,
    /// ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
    pub created_at: String,
    pub updated_at: String,
    /// Project-level overrides as raw JSON text (`NULL` when unset).
    pub settings_json: Option<String>,
}

pub struct ProjectRepo<'a> {
    conn: &'a Connection,
}

const COLUMNS: &str = "id, name, source_video_path, status, created_at, updated_at, settings_json";

impl<'a> ProjectRepo<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    /// Insert a new project. Fails with `DbError::Conflict` on a duplicate id.
    pub fn insert(&self, project: &Project) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at, settings_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    project.id,
                    project.name,
                    project.source_video_path,
                    project.status.as_str(),
                    project.created_at,
                    project.updated_at,
                    project.settings_json,
                ],
            )
            .map_err(map_insert_err)?;
        Ok(())
    }

    /// Load one project by id (`None` when missing).
    pub fn get(&self, id: &str) -> Result<Option<Project>, DbError> {
        self.conn
            .query_row(
                &format!("SELECT {COLUMNS} FROM projects WHERE id = ?1"),
                params![id],
                row_to_project,
            )
            .optional()
            .map_err(DbError::from)
    }

    /// Find a project that already references the same source video path.
    /// Paths are compared case-insensitively and separator-normalized so
    /// re-importing the same file never creates a duplicate project.
    pub fn find_by_source_path(&self, path: &str) -> Result<Option<Project>, DbError> {
        let needle = normalize_source_path(path);
        let mut stmt = self
            .conn
            .prepare(&format!("SELECT {COLUMNS} FROM projects"))?;
        let rows = stmt.query_map([], row_to_project)?;
        let mut found = None;
        for row in rows {
            let project = row?;
            if normalize_source_path(&project.source_video_path) == needle {
                found = Some(project);
                break;
            }
        }
        Ok(found)
    }

    /// All projects, most recently updated first.
    pub fn list(&self) -> Result<Vec<Project>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM projects ORDER BY updated_at DESC"
        ))?;
        let rows = stmt.query_map([], row_to_project)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Persist every editable field of an existing project. Returns `false`
    /// when no row matched (project not found).
    pub fn update(&self, project: &Project) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "UPDATE projects
                 SET name = ?2, source_video_path = ?3, status = ?4,
                     updated_at = ?5, settings_json = ?6
                 WHERE id = ?1",
            params![
                project.id,
                project.name,
                project.source_video_path,
                project.status.as_str(),
                project.updated_at,
                project.settings_json,
            ],
        )?;
        Ok(n > 0)
    }

    /// Record the current time as `updated_at` (auto-save). Returns `false`
    /// when no row matched.
    pub fn touch(&self, id: &str, now: &str) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "UPDATE projects SET updated_at = ?2 WHERE id = ?1",
            params![id, now],
        )?;
        Ok(n > 0)
    }

    /// Delete a project by id. Returns `false` when no row matched.
    pub fn delete(&self, id: &str) -> Result<bool, DbError> {
        let n = self
            .conn
            .execute("DELETE FROM projects WHERE id = ?1", params![id])?;
        Ok(n > 0)
    }
}

/// Normalize a source video path for duplicate detection: lowercase (Dedup is
/// case-insensitive on Windows) and unify separators so `C:\Videos\A.mp4` and
/// `c:/videos/a.mp4` are treated as the same file.
fn normalize_source_path(path: &str) -> String {
    path.trim().to_lowercase().replace('\\', "/")
}

fn row_to_project(row: &rusqlite::Row<'_>) -> rusqlite::Result<Project> {
    let status_raw: String = row.get(3)?;
    let status = ProjectStatus::from_db_str(&status_raw).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            3,
            rusqlite::types::Type::Text,
            std::io::Error::other(format!("invalid project status: {status_raw:?}")).into(),
        )
    })?;
    Ok(Project {
        id: row.get(0)?,
        name: row.get(1)?,
        source_video_path: row.get(2)?,
        status,
        created_at: row.get(4)?,
        updated_at: row.get(5)?,
        settings_json: row.get(6)?,
    })
}

/// Surface UNIQUE/PK constraint violations as `Conflict` (insert is a full CRUD
/// "create" that must report duplicates distinctly from IO failures).
fn map_insert_err(e: rusqlite::Error) -> DbError {
    match &e {
        rusqlite::Error::SqliteFailure(err, _)
            if err.code == rusqlite::ErrorCode::ConstraintViolation =>
        {
            DbError::Conflict(e.to_string())
        }
        _ => DbError::from(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{new_uuid_v4, utc_iso8601_now, Database};

    fn repo(label: &str) -> (Database, std::path::PathBuf) {
        let dir =
            std::env::temp_dir().join(format!("tooltranslate_repo_{label}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Database::open(&dir.join("app.db")).expect("open");
        (db, dir)
    }

    fn sample(name: &str) -> Project {
        Project {
            id: new_uuid_v4().expect("uuid"),
            name: name.into(),
            source_video_path: format!("{name}.mp4"),
            status: ProjectStatus::Draft,
            created_at: utc_iso8601_now(),
            updated_at: utc_iso8601_now(),
            settings_json: None,
        }
    }

    #[test]
    fn insert_get_roundtrip() {
        let (db, dir) = repo("r1");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let project = sample("roundtrip");
        repo.insert(&project).expect("insert");
        let loaded = repo.get(&project.id).expect("get").expect("row");
        assert_eq!(loaded, project);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn get_missing_returns_none() {
        let (db, dir) = repo("r2");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        assert!(repo.get(&new_uuid_v4().unwrap()).expect("get").is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_returns_all_ordered() {
        let (db, dir) = repo("r3");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let a = sample("a");
        let b = sample("b");
        repo.insert(&a).expect("insert a");
        repo.insert(&b).expect("insert b");
        let list = repo.list().expect("list");
        assert_eq!(list.len(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_persists_all_editable_fields() {
        let (db, dir) = repo("r4");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let mut project = sample("before");
        repo.insert(&project).expect("insert");
        project.name = "after".into();
        project.source_video_path = "new.mp4".into();
        project.status = ProjectStatus::Transcribed;
        project.settings_json = Some("{\"lang\":\"vi\"}".into());
        project.updated_at = utc_iso8601_now();
        assert!(repo.update(&project).expect("update"));
        assert_eq!(repo.get(&project.id).expect("get").expect("row"), project);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn touch_bumps_updated_at_and_update_missing_is_false() {
        let (db, dir) = repo("r5");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let project = sample("touch");
        repo.insert(&project).expect("insert");
        assert!(repo
            .touch(&project.id, "2026-08-10T10:00:00.000Z")
            .expect("touch"));
        let loaded = repo.get(&project.id).expect("get").expect("row");
        assert_eq!(loaded.updated_at, "2026-08-10T10:00:00.000Z");
        assert!(!repo
            .touch("00000000-0000-4000-8000-000000000000", "x")
            .expect("touch missing"));
        assert!(!repo.update(&sample("missing")).expect("update missing"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn delete_removes_and_reports_missing() {
        let (db, dir) = repo("r6");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let project = sample("delete");
        repo.insert(&project).expect("insert");
        assert!(repo.delete(&project.id).expect("delete"));
        assert!(repo.get(&project.id).expect("get").is_none());
        assert!(!repo.delete(&project.id).expect("delete again"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn find_by_source_path_matches_normalized_and_case_insensitive() {
        let (db, dir) = repo("r8");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let mut project = sample("source");
        project.source_video_path = "C:\\Videos\\Dự án\\A.MP4".into();
        repo.insert(&project).expect("insert");

        // Exact match (Windows-style path).
        let found = repo
            .find_by_source_path("C:\\Videos\\Dự án\\A.MP4")
            .expect("find")
            .expect("row");
        assert_eq!(found.id, project.id);

        // Case + separator variations resolve to the same project.
        for variant in [
            "c:/videos/dự án/a.mp4",
            "C:/VIDEOS/Dự án/a.mp4",
            "c:\\videos\\dự án\\a.mp4",
        ] {
            let found = repo
                .find_by_source_path(variant)
                .expect("find")
                .expect("row");
            assert_eq!(found.name, project.name, "variant {variant:?} must match");
        }

        // A truly different file is not matched.
        assert!(repo
            .find_by_source_path("C:\\Videos\\B.mp4")
            .expect("find")
            .is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn find_by_source_path_returns_first_match_only() {
        let (db, dir) = repo("r9");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let mut a = sample("first");
        a.source_video_path = "Z:\\same.mp4".into();
        let mut b = sample("second");
        b.source_video_path = "z:/same.mp4".into();
        repo.insert(&a).expect("insert a");
        repo.insert(&b).expect("insert b");

        let found = repo
            .find_by_source_path("Z:\\SAME.MP4")
            .expect("find")
            .expect("row");
        assert_eq!(found.id, a.id, "oldest matching project wins");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn insert_duplicate_id_conflicts() {
        let (db, dir) = repo("r7");
        let conn = db.conn();
        let repo = ProjectRepo::new(&conn);
        let project = sample("dup");
        repo.insert(&project).expect("insert");
        let err = repo.insert(&project).expect_err("duplicate must conflict");
        assert!(matches!(err, DbError::Conflict(_)), "got {err:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
