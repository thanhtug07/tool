//! Job repository (TASK-010): full CRUD for the `jobs` table.
//!
//! The wire shape is canonical: `schemas/job.schema.json` (TASK-007
//! single-source-of-truth) mirrors `MASTER_PLAN.md` §17.1 `jobs` table + §24.4
//! Job object. Status values on the wire are the schema enum
//! (`queued/running/succeeded/failed/cancelled`); the DB stores the same
//! lowercase strings.
//!
//! Internal columns that are *not* part of the wire contract (retry bookkeeping,
//! the persisted cancel flag, the full error log and `updated_at`) are kept on
//! the row but marked `#[serde(skip)]` so the IPC payload never carries them.

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::db::DbError;

/// Kind of work a job performs (canonical `job.schema.json` `JobType`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum JobType {
    Transcribe,
    Translate,
    Subtitle,
    Tts,
    Render,
}

impl JobType {
    pub fn as_str(&self) -> &'static str {
        match self {
            JobType::Transcribe => "transcribe",
            JobType::Translate => "translate",
            JobType::Subtitle => "subtitle",
            JobType::Tts => "tts",
            JobType::Render => "render",
        }
    }

    pub fn from_db_str(s: &str) -> Option<JobType> {
        match s {
            "transcribe" => Some(JobType::Transcribe),
            "translate" => Some(JobType::Translate),
            "subtitle" => Some(JobType::Subtitle),
            "tts" => Some(JobType::Tts),
            "render" => Some(JobType::Render),
            _ => None,
        }
    }
}

/// Lifecycle state of a job (canonical `job.schema.json` `JobStatus`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    #[default]
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

impl JobStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            JobStatus::Queued => "queued",
            JobStatus::Running => "running",
            JobStatus::Succeeded => "succeeded",
            JobStatus::Failed => "failed",
            JobStatus::Cancelled => "cancelled",
        }
    }

    pub fn from_db_str(s: &str) -> Option<JobStatus> {
        match s {
            "queued" => Some(JobStatus::Queued),
            "running" => Some(JobStatus::Running),
            "succeeded" => Some(JobStatus::Succeeded),
            "failed" => Some(JobStatus::Failed),
            "cancelled" => Some(JobStatus::Cancelled),
            _ => None,
        }
    }

    /// Whether a transition out of this state is allowed. Guarded by
    /// `JobService` before every state change.
    ///
    /// `Running → Queued` exists for the crash-resume path: a row found in
    /// `running` after a restart is returned to the queue (TASK-010 resume).
    pub fn can_transition(self, to: JobStatus) -> bool {
        matches!(
            (self, to),
            (JobStatus::Queued, JobStatus::Running)
                | (JobStatus::Queued, JobStatus::Cancelled)
                | (JobStatus::Running, JobStatus::Queued)
                | (JobStatus::Running, JobStatus::Succeeded)
                | (JobStatus::Running, JobStatus::Failed)
                | (JobStatus::Running, JobStatus::Cancelled)
                | (JobStatus::Failed, JobStatus::Queued)
                | (JobStatus::Cancelled, JobStatus::Queued)
        )
    }

    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled
        )
    }
}

/// A job (wire + DB representation).
///
/// Serializes exactly to `schemas/job.schema.json` (the `type` field maps to
/// `job_type`). Internal row columns are skipped over IPC.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Job {
    /// `job_NNNN` (canonical id pattern `^job_[0-9]+$`).
    pub id: String,
    pub project_id: String,
    #[serde(rename = "type")]
    pub job_type: JobType,
    pub status: JobStatus,
    /// 0..1 pipeline progress.
    pub progress: f64,
    /// Sub-stage label (non-empty on the wire).
    pub stage: String,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    /// Free-form params object (canonical schema: object).
    pub params: Value,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,

    // ---- DB-internal (never serialized) ----
    #[serde(skip)]
    pub error_log: Option<String>,
    #[serde(skip)]
    pub retry_count: u32,
    #[serde(skip)]
    pub cancel_requested: bool,
    #[serde(skip)]
    pub updated_at: String,
}

pub struct JobRepo<'a> {
    conn: &'a Connection,
}

const COLUMNS: &str = "id, project_id, type, status, progress, stage, error_code, \
                       error_message, error_log, params_json, retry_count, cancel_requested, \
                       created_at, updated_at, started_at, finished_at";

impl<'a> JobRepo<'a> {
    pub fn new(conn: &'a Connection) -> Self {
        Self { conn }
    }

    /// Insert a new job. Fails with `DbError::Conflict` on a duplicate id.
    pub fn insert(&self, job: &Job) -> Result<(), DbError> {
        self.conn
            .execute(
                "INSERT INTO jobs (id, project_id, type, status, progress, stage, error_code,
                                   error_message, error_log, params_json, retry_count,
                                   cancel_requested, created_at, updated_at, started_at, finished_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
                params![
                    job.id,
                    job.project_id,
                    job.job_type.as_str(),
                    job.status.as_str(),
                    job.progress,
                    job.stage,
                    job.error_code,
                    job.error_message,
                    job.error_log,
                    job.params.to_string(),
                    job.retry_count,
                    job.cancel_requested as i64,
                    job.created_at,
                    job.updated_at,
                    job.started_at,
                    job.finished_at,
                ],
            )
            .map_err(map_insert_err)?;
        Ok(())
    }

    /// Load one job by id (`None` when missing).
    pub fn get(&self, id: &str) -> Result<Option<Job>, DbError> {
        self.conn
            .query_row(
                &format!("SELECT {COLUMNS} FROM jobs WHERE id = ?1"),
                params![id],
                row_to_job,
            )
            .optional()
            .map_err(DbError::from)
    }

    /// All jobs for a project, most recently updated first.
    pub fn list_by_project(&self, project_id: &str) -> Result<Vec<Job>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM jobs WHERE project_id = ?1 ORDER BY updated_at DESC"
        ))?;
        let rows = stmt.query_map(params![project_id], row_to_job)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// All jobs across every project, most recently updated first (Dashboard
    /// feed). Bounded by `limit` so the IPC payload stays small.
    pub fn list_recent(&self, limit: u32) -> Result<Vec<Job>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM jobs ORDER BY updated_at DESC LIMIT ?1"
        ))?;
        let rows = stmt.query_map(params![limit.max(1)], row_to_job)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Jobs that are `queued` (oldest first) — used to seed the in-memory FIFO
    /// queue at startup so queued work survives an app restart.
    pub fn list_queued(&self) -> Result<Vec<Job>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
        ))?;
        let rows = stmt.query_map([], row_to_job)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Jobs left in `running` after a crash — they must be resumed (returned to
    /// `queued`) on the next startup.
    pub fn list_running(&self) -> Result<Vec<Job>, DbError> {
        let mut stmt = self.conn.prepare(&format!(
            "SELECT {COLUMNS} FROM jobs WHERE status = 'running' ORDER BY created_at ASC"
        ))?;
        let rows = stmt.query_map([], row_to_job)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Persist every editable field of an existing job. Returns `false` when no
    /// row matched.
    pub fn update(&self, job: &Job) -> Result<bool, DbError> {
        let n = self.conn.execute(
            "UPDATE jobs
                SET project_id = ?2, type = ?3, status = ?4, progress = ?5, stage = ?6,
                    error_code = ?7, error_message = ?8, error_log = ?9, params_json = ?10,
                    retry_count = ?11, cancel_requested = ?12, updated_at = ?13,
                    started_at = ?14, finished_at = ?15
              WHERE id = ?1",
            params![
                job.id,
                job.project_id,
                job.job_type.as_str(),
                job.status.as_str(),
                job.progress,
                job.stage,
                job.error_code,
                job.error_message,
                job.error_log,
                job.params.to_string(),
                job.retry_count,
                job.cancel_requested as i64,
                job.updated_at,
                job.started_at,
                job.finished_at,
            ],
        )?;
        Ok(n > 0)
    }

    /// Next monotonic `job_NNNN` id (inclusive of the highest existing id).
    ///
    /// Must be called inside a transaction so concurrent submits cannot claim
    /// the same id.
    pub fn next_id(&self) -> Result<String, DbError> {
        let n: i64 = self.conn.query_row(
            "SELECT COALESCE(MAX(CAST(substr(id, 5) AS INTEGER)), 0) + 1 FROM jobs",
            [],
            |r| r.get(0),
        )?;
        Ok(format!("job_{n:04}"))
    }
}

fn row_to_job(row: &rusqlite::Row<'_>) -> rusqlite::Result<Job> {
    let type_raw: String = row.get(2)?;
    let status_raw: String = row.get(3)?;
    let job_type = JobType::from_db_str(&type_raw).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            2,
            rusqlite::types::Type::Text,
            std::io::Error::other(format!("invalid job type: {type_raw:?}")).into(),
        )
    })?;
    let status = JobStatus::from_db_str(&status_raw).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            3,
            rusqlite::types::Type::Text,
            std::io::Error::other(format!("invalid job status: {status_raw:?}")).into(),
        )
    })?;
    let params_raw: String = row.get(9)?;
    let params: Value = serde_json::from_str(&params_raw).unwrap_or_else(|_| json!({}));
    Ok(Job {
        id: row.get(0)?,
        project_id: row.get(1)?,
        job_type,
        status,
        progress: row.get(4)?,
        stage: row.get(5)?,
        error_code: row.get(6)?,
        error_message: row.get(7)?,
        error_log: row.get(8)?,
        params,
        retry_count: row.get(10)?,
        cancel_requested: row.get::<_, i64>(11)? != 0,
        created_at: row.get(12)?,
        updated_at: row.get(13)?,
        started_at: row.get(14)?,
        finished_at: row.get(15)?,
    })
}

/// Surface duplicate-id constraint violations as `Conflict` so a duplicate job
/// id is reported distinctly from other failures (e.g. FK violations, which
/// stay `Sqlite`). Extended codes: `SQLITE_CONSTRAINT_PRIMARYKEY` = 1555,
/// `SQLITE_CONSTRAINT_UNIQUE` = 2067.
fn map_insert_err(e: rusqlite::Error) -> DbError {
    match &e {
        rusqlite::Error::SqliteFailure(err, _)
            if err.code == rusqlite::ErrorCode::ConstraintViolation
                && matches!(err.extended_code, 1555 | 2067) =>
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
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_job_repo_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let db = Database::open(&dir.join("app.db")).expect("open");
        (db, dir)
    }

    /// Seed a project row (jobs reference `projects(id)`).
    fn seed_project(conn: &Connection) -> String {
        let id = new_uuid_v4().expect("uuid");
        let now = utc_iso8601_now();
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, ?2, ?3, 'draft', ?4, ?4)",
            params![id, "seed", "seed.mp4", now],
        )
        .expect("seed project");
        id
    }

    fn sample(project_id: &str, job_type: JobType) -> Job {
        let now = utc_iso8601_now();
        Job {
            id: "job_0001".into(),
            project_id: project_id.into(),
            job_type,
            status: JobStatus::Queued,
            progress: 0.0,
            stage: "queued".into(),
            error_code: None,
            error_message: None,
            error_log: None,
            params: json!({ "model": "turbo" }),
            created_at: now.clone(),
            updated_at: now,
            started_at: None,
            finished_at: None,
            retry_count: 0,
            cancel_requested: false,
        }
    }

    #[test]
    fn insert_get_roundtrip_preserves_wire_fields() {
        let (db, dir) = repo("r1");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);
        let job = sample(&project_id, JobType::Transcribe);
        repo.insert(&job).expect("insert");
        let loaded = repo.get(&job.id).expect("get").expect("row");
        assert_eq!(loaded, job);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn get_missing_returns_none() {
        let (db, dir) = repo("r2");
        let conn = db.conn();
        let repo = JobRepo::new(&conn);
        assert!(repo.get("job_9999").expect("get").is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_by_project_filters_and_orders() {
        let (db, dir) = repo("r3");
        let conn = db.conn();
        let repo = JobRepo::new(&conn);
        let a = seed_project(&conn);
        let b = seed_project(&conn);

        let job_a1 = sample(&a, JobType::Render);
        let job_a2 = Job {
            id: "job_0002".into(),
            // Deterministically newer so the `updated_at DESC` order is stable
            // regardless of clock resolution.
            updated_at: "2099-01-01T00:00:00.000Z".into(),
            ..sample(&a, JobType::Transcribe)
        };
        let job_b = Job {
            id: "job_0003".into(),
            ..sample(&b, JobType::Subtitle)
        };
        repo.insert(&job_a1).expect("a1");
        repo.insert(&job_a2).expect("a2");
        repo.insert(&job_b).expect("b");

        let list = repo.list_by_project(&a).expect("list a");
        assert_eq!(list.len(), 2);
        assert!(list.iter().all(|j| j.project_id == a));
        let ids: Vec<_> = list.iter().map(|j| j.id.as_str()).collect();
        assert_eq!(ids, ["job_0002", "job_0001"]); // newest updated first
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_persists_all_editable_fields() {
        let (db, dir) = repo("r4");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);
        let mut job = sample(&project_id, JobType::Transcribe);
        repo.insert(&job).expect("insert");

        job.status = JobStatus::Running;
        job.progress = 0.42;
        job.stage = "transcribing".into();
        job.started_at = Some(utc_iso8601_now());
        job.updated_at = utc_iso8601_now();
        job.retry_count = 2;
        job.cancel_requested = true;
        assert!(repo.update(&job).expect("update"));

        assert_eq!(repo.get(&job.id).expect("get").expect("row"), job);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn update_missing_returns_false() {
        let (db, dir) = repo("r5");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);
        assert!(!repo
            .update(&sample(&project_id, JobType::Render))
            .expect("update"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_recent_returns_all_projects_ordered() {
        let (db, dir) = repo("r_recent");
        let conn = db.conn();
        let repo = JobRepo::new(&conn);
        let a = seed_project(&conn);
        let b = seed_project(&conn);
        let job_a = sample(&a, JobType::Transcribe);
        let job_b = Job {
            id: "job_0002".into(),
            updated_at: "2099-01-01T00:00:00.000Z".into(),
            ..sample(&b, JobType::Render)
        };
        let job_c = Job {
            id: "job_0003".into(),
            updated_at: "2098-01-01T00:00:00.000Z".into(),
            ..sample(&a, JobType::Subtitle)
        };
        repo.insert(&job_a).expect("insert a");
        repo.insert(&job_b).expect("insert b");
        repo.insert(&job_c).expect("insert c");

        let all = repo.list_recent(10).expect("list recent");
        assert_eq!(all.len(), 3);
        let ids: Vec<_> = all.iter().map(|j| j.id.as_str()).collect();
        assert_eq!(ids, ["job_0002", "job_0003", "job_0001"]); // updated_at DESC

        let capped = repo.list_recent(2).expect("capped");
        assert_eq!(capped.len(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn queued_and_running_scans_for_resume() {
        let (db, dir) = repo("r6");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);

        let queued = sample(&project_id, JobType::Transcribe);
        let running = Job {
            id: "job_0002".into(),
            status: JobStatus::Running,
            stage: "transcribing".into(),
            progress: 0.6,
            ..sample(&project_id, JobType::Translate)
        };
        let done = Job {
            id: "job_0003".into(),
            status: JobStatus::Succeeded,
            ..sample(&project_id, JobType::Render)
        };
        repo.insert(&queued).expect("queued");
        repo.insert(&running).expect("running");
        repo.insert(&done).expect("done");

        let q = repo.list_queued().expect("queued scan");
        assert_eq!(q.len(), 1);
        assert_eq!(q[0].id, "job_0001");

        let r = repo.list_running().expect("running scan");
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].id, "job_0002");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn next_id_is_monotonic() {
        let (db, dir) = repo("r7");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);

        let first = repo.next_id().expect("first");
        assert_eq!(first, "job_0001");

        let job = sample(&project_id, JobType::Transcribe);
        repo.insert(&job).expect("insert");
        let second = repo.next_id().expect("second");
        assert_eq!(second, "job_0002");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn insert_duplicate_id_conflicts() {
        let (db, dir) = repo("r8");
        let conn = db.conn();
        let project_id = seed_project(&conn);
        let repo = JobRepo::new(&conn);
        let job = sample(&project_id, JobType::Transcribe);
        repo.insert(&job).expect("insert");
        let err = repo.insert(&job).expect_err("duplicate must conflict");
        assert!(matches!(err, DbError::Conflict(_)), "got {err:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn insert_unknown_project_violates_foreign_key() {
        let (db, dir) = repo("r9");
        let conn = db.conn();
        let repo = JobRepo::new(&conn);
        let job = sample("00000000-0000-4000-8000-000000000000", JobType::Transcribe);
        let err = repo
            .insert(&job)
            .expect_err("FK must reject unknown project");
        assert!(matches!(err, DbError::Sqlite(_)), "got {err:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn can_transition_accepts_lifecycle_edges() {
        use JobStatus::{Cancelled, Failed, Queued, Running, Succeeded};
        let allowed = [
            (Queued, Running),
            (Queued, Cancelled),
            (Running, Queued), // crash-resume path
            (Running, Succeeded),
            (Running, Failed),
            (Running, Cancelled),
            (Failed, Queued), // manual retry
            (Cancelled, Queued),
        ];
        for (from, to) in allowed {
            assert!(from.can_transition(to), "{from:?} → {to:?} must be allowed");
        }
    }

    #[test]
    fn can_transition_rejects_invalid_edges() {
        use JobStatus::{Cancelled, Failed, Queued, Running, Succeeded};
        let denied = [
            (Queued, Queued),
            (Queued, Succeeded),
            (Queued, Failed),
            (Running, Running),
            (Succeeded, Queued),
            (Succeeded, Running),
            (Succeeded, Failed),
            (Succeeded, Cancelled),
            (Failed, Succeeded),
            (Failed, Running),
            (Cancelled, Running),
            (Cancelled, Succeeded),
        ];
        for (from, to) in denied {
            assert!(!from.can_transition(to), "{from:?} → {to:?} must be denied");
        }
    }

    #[test]
    fn terminal_states_are_recognized() {
        use JobStatus::{Cancelled, Failed, Queued, Running, Succeeded};
        assert!(Succeeded.is_terminal());
        assert!(Failed.is_terminal());
        assert!(Cancelled.is_terminal());
        assert!(!Queued.is_terminal());
        assert!(!Running.is_terminal());
    }
}
