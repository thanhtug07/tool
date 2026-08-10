//! ProjectService (TASK-008).
//!
//! Public interface `create/load/save/delete` (MASTER_PLAN.md §23, §25.1 IPC).
//! Owns the SQLite database and the per-project working directories under the
//! OS user-data directory:
//!
//! ```text
//! {user_data}/app.db
//! {user_data}/projects/{project_id}/{video,cache,output}
//! ```
//!
//! Project ids are UUID v4 only, so the id→directory mapping can never escape
//! the projects tree (no path traversal). Names and video paths are validated
//! and stored as plain strings; they are never used as directory names.

use std::fs;
use std::path::{Path, PathBuf};

use crate::db::repo::project::ProjectRepo;
use crate::db::{
    is_valid_uuid_v4, new_uuid_v4, utc_iso8601_now, Database, DbError, Project, ProjectStatus,
};

const MAX_NAME_LEN: usize = 200;
const MAX_PATH_BYTES: usize = 4096;

/// Sub-directories created for every project.
const PROJECT_SUBDIRS: [&str; 3] = ["video", "cache", "output"];

/// The project persistence service, managed as Tauri app state.
pub struct ProjectService {
    data_dir: PathBuf,
    db: Result<Database, DbError>,
}

impl ProjectService {
    /// Initialize the persistence layer rooted at `data_dir`.
    ///
    /// DB file lives at `{data_dir}/app.db`. Init failures are captured so the
    /// app keeps running and IPC commands surface a clean, descriptive error
    /// instead of panicking on a missing managed state.
    pub fn open(data_dir: PathBuf) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("project database init failed: {e}");
        }
        Self { data_dir, db }
    }

    /// The OS user-data directory this service is rooted at.
    pub fn data_dir(&self) -> &Path {
        &self.data_dir
    }

    /// Path of the SQLite database file.
    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("app.db")
    }

    /// Working directory of a project (validated id), e.g. `.../projects/{id}`.
    pub fn project_dir(&self, id: &str) -> PathBuf {
        self.data_dir.join("projects").join(id)
    }

    /// Create a project: validate input, create the working directories, insert
    /// the row, and return the persisted project.
    pub fn create(&self, name: String, source_video_path: String) -> Result<Project, DbError> {
        let name = validate_name(name)?;
        let source_video_path = validate_source_path(&source_video_path)?;
        let id = new_uuid_v4()?;
        let now = utc_iso8601_now();
        let project = Project {
            id,
            name,
            source_video_path,
            status: ProjectStatus::Draft,
            created_at: now.clone(),
            updated_at: now,
            settings_json: None,
        };

        self.ensure_project_dirs(&project.id)?;
        // Dir-then-row so a failed insert leaves no orphan row; on insert
        // failure the directories are rolled back too.
        self.db()?
            .transaction(|conn| ProjectRepo::new(conn).insert(&project))
            .inspect_err(|_| self.remove_project_dir(&project.id))?;
        Ok(project)
    }

    /// Load a project by id.
    pub fn load(&self, id: &str) -> Result<Project, DbError> {
        let id = validate_id(id)?;
        let conn = self.db()?.conn();
        ProjectRepo::new(&conn)
            .get(&id)?
            .ok_or_else(|| DbError::NotFound(format!("project {id} does not exist")))
    }

    /// Auto-save: record the current time as `updated_at` (MASTER_PLAN §25.1
    /// `project.save(id) → void`).
    pub fn save(&self, id: &str) -> Result<(), DbError> {
        let id = validate_id(id)?;
        let now = utc_iso8601_now();
        let conn = self.db()?.conn();
        let touched = ProjectRepo::new(&conn).touch(&id, &now)?;
        if !touched {
            return Err(DbError::NotFound(format!("project {id} does not exist")));
        }
        Ok(())
    }

    /// Delete a project: remove the row, then the working directory.
    pub fn delete(&self, id: &str) -> Result<(), DbError> {
        let id = validate_id(id)?;
        let conn = self.db()?.conn();
        let removed = ProjectRepo::new(&conn).delete(&id)?;
        if !removed {
            return Err(DbError::NotFound(format!("project {id} does not exist")));
        }
        self.remove_project_dir(&id);
        Ok(())
    }

    /// All projects, most recently updated first (Dashboard support).
    pub fn list(&self) -> Result<Vec<Project>, DbError> {
        let conn = self.db()?.conn();
        ProjectRepo::new(&conn).list()
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }

    fn ensure_project_dirs(&self, id: &str) -> Result<(), DbError> {
        let base = self.project_dir(id);
        fs::create_dir_all(&base)?;
        for sub in PROJECT_SUBDIRS {
            fs::create_dir_all(base.join(sub))?;
        }
        Ok(())
    }

    fn remove_project_dir(&self, id: &str) {
        let dir = self.project_dir(id);
        if let Err(e) = fs::remove_dir_all(&dir) {
            log::warn!("failed to remove project dir {}: {e}", dir.display());
        }
    }
}

fn validate_name(name: String) -> Result<String, DbError> {
    let trimmed = name.trim().to_string();
    if trimmed.is_empty() {
        return Err(DbError::InvalidInput(
            "project name must not be empty".into(),
        ));
    }
    if trimmed.chars().count() > MAX_NAME_LEN {
        return Err(DbError::InvalidInput(format!(
            "project name exceeds {MAX_NAME_LEN} characters"
        )));
    }
    Ok(trimmed)
}

fn validate_source_path(path: &str) -> Result<String, DbError> {
    if path.trim().is_empty() {
        return Err(DbError::InvalidInput(
            "source video path must not be empty".into(),
        ));
    }
    if path.contains('\0') {
        return Err(DbError::InvalidInput(
            "source video path contains a NUL byte".into(),
        ));
    }
    if path.len() > MAX_PATH_BYTES {
        return Err(DbError::InvalidInput(format!(
            "source video path exceeds {MAX_PATH_BYTES} bytes"
        )));
    }
    Ok(path.to_string())
}

/// Project ids become directory names, so only strict UUID v4 is accepted —
/// the check doubles as a path-traversal guard.
fn validate_id(id: &str) -> Result<String, DbError> {
    if !is_valid_uuid_v4(id) {
        return Err(DbError::InvalidInput(format!("invalid project id: {id:?}")));
    }
    Ok(id.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::repo::project::ProjectRepo;

    fn test_data_dir(label: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("tooltranslate_svc_{label}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir
    }

    fn service(label: &str) -> ProjectService {
        let svc = ProjectService::open(test_data_dir(label));
        assert!(svc.db().is_ok(), "db must open cleanly for tests");
        svc
    }

    #[test]
    fn create_persists_row_and_working_dirs() {
        let svc = service("create");
        let project = svc
            .create("Đoạn phim mẫu".into(), "C:\\Videos\\mẫu.mp4".into())
            .expect("create");
        assert!(is_valid_uuid_v4(&project.id));
        assert_eq!(project.status, ProjectStatus::Draft);
        assert_eq!(project.created_at, project.updated_at);
        assert!(project.settings_json.is_none());
        for sub in PROJECT_SUBDIRS {
            assert!(
                svc.project_dir(&project.id).join(sub).is_dir(),
                "missing {sub} dir"
            );
        }
        assert_eq!(svc.load(&project.id).expect("load"), project);
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn load_and_save_roundtrip_with_auto_save_bump() {
        let svc = service("save");
        // Seed a row with a fixed old timestamp, then auto-save it.
        let old = "2000-01-01T00:00:00.000Z";
        let seed = Project {
            id: new_uuid_v4().expect("uuid"),
            name: "seed".into(),
            source_video_path: "seed.mp4".into(),
            status: ProjectStatus::Draft,
            created_at: old.into(),
            updated_at: old.into(),
            settings_json: None,
        };
        {
            let db = svc.db().expect("db");
            let conn = db.conn();
            ProjectRepo::new(&conn).insert(&seed).expect("seed insert");
        }
        svc.save(&seed.id).expect("save");
        let loaded = svc.load(&seed.id).expect("load");
        assert_eq!(loaded.created_at, old, "create time is immutable");
        assert!(
            loaded.updated_at.as_str() > old,
            "auto-save must move updated_at forward"
        );
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn delete_removes_row_and_dirs() {
        let svc = service("delete");
        let project = svc
            .create("to delete".into(), "v.mp4".into())
            .expect("create");
        let id = project.id.clone();
        svc.delete(&id).expect("delete");
        assert!(matches!(svc.load(&id), Err(DbError::NotFound(_))));
        assert!(!svc.project_dir(&id).exists());
        // Second delete reports not-found.
        assert!(matches!(svc.delete(&id), Err(DbError::NotFound(_))));
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn invalid_inputs_are_rejected() {
        let svc = service("invalid");
        assert!(matches!(
            svc.create(String::new(), "v.mp4".into()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.create("   ".into(), "v.mp4".into()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.create("ok".into(), String::new()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.create("ok".into(), "bad\0path".into()),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.load("not-a-uuid"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.save("not-a-uuid"),
            Err(DbError::InvalidInput(_))
        ));
        assert!(matches!(
            svc.delete("../../outside"),
            Err(DbError::InvalidInput(_))
        ));
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn missing_project_is_not_found() {
        let svc = service("missing");
        let id = new_uuid_v4().expect("uuid");
        assert!(matches!(svc.load(&id), Err(DbError::NotFound(_))));
        assert!(matches!(svc.save(&id), Err(DbError::NotFound(_))));
        assert!(matches!(svc.delete(&id), Err(DbError::NotFound(_))));
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn unicode_vietnamese_project_data_roundtrips() {
        let svc = service("unicode");
        let name = "Bộ phim tiếng Việt: Đặc vụ vô hình";
        let path = "D:\\Phim\\Việt Nam\\Đặc vụ 無形.mp4";
        let project = svc.create(name.into(), path.into()).expect("create");
        let loaded = svc.load(&project.id).expect("load");
        assert_eq!(loaded.name, name);
        assert_eq!(loaded.source_video_path, path);
        let _ = fs::remove_dir_all(svc.data_dir());
    }

    #[test]
    fn restart_persists_across_service_reopen() {
        let dir = test_data_dir("restart");
        let id = {
            let svc = ProjectService::open(dir.clone());
            let project = svc
                .create("persist me".into(), "D:\\videos\\a.mp4".into())
                .expect("create");
            project.id.clone()
        };
        // Simulate an app restart: a brand-new service over the same data dir.
        let svc = ProjectService::open(dir.clone());
        let loaded = svc.load(&id).expect("load after restart");
        assert_eq!(loaded.name, "persist me");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn two_instances_do_not_overwrite_each_other() {
        // Two services over the same data dir emulate two app instances sharing
        // one SQLite file. WAL + busy_timeout must keep both usable.
        let dir = test_data_dir("two_instances");
        let a = ProjectService::open(dir.clone());
        let b = ProjectService::open(dir.clone());

        let pa = a.create("A".into(), "va.mp4".into()).expect("create A");
        let pb = b.create("B".into(), "vb.mp4".into()).expect("create B");

        // Each instance sees both projects — nothing was overwritten.
        assert_eq!(a.list().expect("list a").len(), 2);
        assert_eq!(b.list().expect("list b").len(), 2);

        // Auto-saving A via instance a must not touch B's row.
        a.save(&pa.id).expect("save A");
        let pb_after = b.load(&pb.id).expect("load B");
        let pa_after = a.load(&pa.id).expect("load A");
        assert_eq!(pb_after.name, "B");
        assert_eq!(pa_after.name, "A");
        assert!(pa_after.updated_at >= pa.updated_at);
        assert_eq!(pb_after.updated_at, pb.updated_at);

        // Deleting via b must not affect a's view of A.
        b.delete(&pb.id).expect("delete B");
        assert_eq!(a.list().expect("list after delete").len(), 1);
        assert!(a.load(&pa.id).is_ok());
        let _ = fs::remove_dir_all(&dir);
    }
}
