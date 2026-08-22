//! Task repository: CRUD for the `tasks` table (migration v9).
//!
//! Tasks represent units of work within a job. The task orchestration layer
//! (TaskRunner) manages task lifecycle, dependency resolution, concurrency,
//! retry, and cancellation.
//!
//! See `docs/TASK_ARCHITECTURE.md` for the full contract.

use crate::db::DbError;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

/// Task type (maps to job stage).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskType {
    Transcribe,
    Translate,
    Subtitle,
    Tts,
    Render,
    Logo,
    Chunk,
    Audio,
}

impl TaskType {
    pub fn as_str(&self) -> &'static str {
        match self {
            TaskType::Transcribe => "transcribe",
            TaskType::Translate => "translate",
            TaskType::Subtitle => "subtitle",
            TaskType::Tts => "tts",
            TaskType::Render => "render",
            TaskType::Logo => "logo",
            TaskType::Chunk => "chunk",
            TaskType::Audio => "audio",
        }
    }

    pub fn from_db_str(s: &str) -> Option<TaskType> {
        match s {
            "transcribe" => Some(TaskType::Transcribe),
            "translate" => Some(TaskType::Translate),
            "subtitle" => Some(TaskType::Subtitle),
            "tts" => Some(TaskType::Tts),
            "render" => Some(TaskType::Render),
            "logo" => Some(TaskType::Logo),
            "chunk" => Some(TaskType::Chunk),
            "audio" => Some(TaskType::Audio),
            _ => None,
        }
    }
}

/// Task lifecycle state (see `docs/TASK_ARCHITECTURE.md` §2).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Queued,
    Ready,
    Running,
    Succeeded,
    Failed,
    Cancelled,
    Blocked,
}

impl TaskStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            TaskStatus::Queued => "queued",
            TaskStatus::Ready => "ready",
            TaskStatus::Running => "running",
            TaskStatus::Succeeded => "succeeded",
            TaskStatus::Failed => "failed",
            TaskStatus::Cancelled => "cancelled",
            TaskStatus::Blocked => "blocked",
        }
    }

    pub fn from_db_str(s: &str) -> Option<TaskStatus> {
        match s {
            "queued" => Some(TaskStatus::Queued),
            "ready" => Some(TaskStatus::Ready),
            "running" => Some(TaskStatus::Running),
            "succeeded" => Some(TaskStatus::Succeeded),
            "failed" => Some(TaskStatus::Failed),
            "cancelled" => Some(TaskStatus::Cancelled),
            "blocked" => Some(TaskStatus::Blocked),
            _ => None,
        }
    }

    /// Whether a transition out of this state is allowed.
    pub fn can_transition(self, to: TaskStatus) -> bool {
        matches!(
            (self, to),
            (TaskStatus::Queued, TaskStatus::Ready)
                | (TaskStatus::Queued, TaskStatus::Blocked)
                | (TaskStatus::Queued, TaskStatus::Cancelled)
                | (TaskStatus::Ready, TaskStatus::Running)
                | (TaskStatus::Ready, TaskStatus::Cancelled)
                | (TaskStatus::Running, TaskStatus::Succeeded)
                | (TaskStatus::Running, TaskStatus::Queued)  // transient fail -> retry
                | (TaskStatus::Running, TaskStatus::Failed)
                | (TaskStatus::Running, TaskStatus::Cancelled)
                | (TaskStatus::Blocked, TaskStatus::Queued)  // dep retry succeeds (V2)
                | (TaskStatus::Blocked, TaskStatus::Cancelled)
        )
    }

    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            TaskStatus::Succeeded | TaskStatus::Failed | TaskStatus::Cancelled
        )
    }
}

/// A task (DB + wire representation).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub job_id: String,
    pub task_type: TaskType,
    pub stage: String,
    pub status: TaskStatus,
    pub progress: f64,
    /// JSON array of task IDs this task depends on.
    pub depends_on: String,
    /// Task-specific parameters.
    pub params_json: Option<String>,
    /// Input fingerprint for idempotency.
    pub input_fingerprint: Option<String>,
    /// Output artifact metadata (path, fingerprint, size).
    pub result_json: Option<String>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub retry_count: i32,
    pub max_attempts: i32,
    pub cancel_requested: bool,
    pub created_at: String,
    pub updated_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
}

/// Create a new task (single INSERT).
pub fn create_task(conn: &Connection, task: &Task) -> Result<(), DbError> {
    conn.execute(
        "INSERT INTO tasks (
            id, job_id, task_type, stage, status, progress,
            depends_on, params_json, input_fingerprint, result_json,
            error_code, error_message, retry_count, max_attempts,
            cancel_requested, created_at, updated_at, started_at, finished_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
        params![
            task.id,
            task.job_id,
            task.task_type.as_str(),
            task.stage,
            task.status.as_str(),
            task.progress,
            task.depends_on,
            task.params_json,
            task.input_fingerprint,
            task.result_json,
            task.error_code,
            task.error_message,
            task.retry_count,
            task.max_attempts,
            task.cancel_requested as i32,
            task.created_at,
            task.updated_at,
            task.started_at,
            task.finished_at,
        ],
    )?;
    Ok(())
}

/// Get a task by ID.
pub fn get_task(conn: &Connection, task_id: &str) -> Result<Option<Task>, DbError> {
    let task = conn
        .query_row(
            "SELECT id, job_id, task_type, stage, status, progress,
                    depends_on, params_json, input_fingerprint, result_json,
                    error_code, error_message, retry_count, max_attempts,
                    cancel_requested, created_at, updated_at, started_at, finished_at
             FROM tasks WHERE id = ?1",
            params![task_id],
            |row| {
                Ok(Task {
                    id: row.get(0)?,
                    job_id: row.get(1)?,
                    task_type: TaskType::from_db_str(&row.get::<_, String>(2)?)
                        .unwrap_or(TaskType::Transcribe),
                    stage: row.get(3)?,
                    status: TaskStatus::from_db_str(&row.get::<_, String>(4)?)
                        .unwrap_or(TaskStatus::Queued),
                    progress: row.get(5)?,
                    depends_on: row.get(6)?,
                    params_json: row.get(7)?,
                    input_fingerprint: row.get(8)?,
                    result_json: row.get(9)?,
                    error_code: row.get(10)?,
                    error_message: row.get(11)?,
                    retry_count: row.get(12)?,
                    max_attempts: row.get(13)?,
                    cancel_requested: row.get::<_, i32>(14)? != 0,
                    created_at: row.get(15)?,
                    updated_at: row.get(16)?,
                    started_at: row.get(17)?,
                    finished_at: row.get(18)?,
                })
            },
        )
        .optional()?;
    Ok(task)
}

/// Get all tasks for a job.
pub fn get_tasks_by_job(conn: &Connection, job_id: &str) -> Result<Vec<Task>, DbError> {
    let mut stmt = conn.prepare(
        "SELECT id, job_id, task_type, stage, status, progress,
                depends_on, params_json, input_fingerprint, result_json,
                error_code, error_message, retry_count, max_attempts,
                cancel_requested, created_at, updated_at, started_at, finished_at
         FROM tasks WHERE job_id = ?1 ORDER BY created_at",
    )?;
    let tasks = stmt
        .query_map(params![job_id], |row| {
            Ok(Task {
                id: row.get(0)?,
                job_id: row.get(1)?,
                task_type: TaskType::from_db_str(&row.get::<_, String>(2)?)
                    .unwrap_or(TaskType::Transcribe),
                stage: row.get(3)?,
                status: TaskStatus::from_db_str(&row.get::<_, String>(4)?)
                    .unwrap_or(TaskStatus::Queued),
                progress: row.get(5)?,
                depends_on: row.get(6)?,
                params_json: row.get(7)?,
                input_fingerprint: row.get(8)?,
                result_json: row.get(9)?,
                error_code: row.get(10)?,
                error_message: row.get(11)?,
                retry_count: row.get(12)?,
                max_attempts: row.get(13)?,
                cancel_requested: row.get::<_, i32>(14)? != 0,
                created_at: row.get(15)?,
                updated_at: row.get(16)?,
                started_at: row.get(17)?,
                finished_at: row.get(18)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(tasks)
}

/// Update task status (with transition guard).
pub fn update_task_status(
    conn: &Connection,
    task_id: &str,
    new_status: TaskStatus,
    now: &str,
) -> Result<bool, DbError> {
    let current =
        get_task(conn, task_id)?.ok_or_else(|| DbError::NotFound(format!("task {task_id}")))?;

    if !current.status.can_transition(new_status) {
        return Ok(false);
    }

    let finished_at = if new_status.is_terminal() {
        Some(now)
    } else {
        None
    };
    let started_at = if new_status == TaskStatus::Running && current.started_at.is_none() {
        Some(now)
    } else {
        current.started_at.as_deref()
    };

    let changed = conn.execute(
        "UPDATE tasks SET status = ?1, updated_at = ?2, finished_at = ?3, started_at = ?4
         WHERE id = ?5 AND status = ?6",
        params![
            new_status.as_str(),
            now,
            finished_at,
            started_at,
            task_id,
            current.status.as_str(),
        ],
    )?;
    Ok(changed > 0)
}

/// Update task progress.
pub fn update_task_progress(
    conn: &Connection,
    task_id: &str,
    progress: f64,
    now: &str,
) -> Result<(), DbError> {
    conn.execute(
        "UPDATE tasks SET progress = ?1, updated_at = ?2 WHERE id = ?3",
        params![progress, now, task_id],
    )?;
    Ok(())
}

/// Update task with result metadata.
pub fn update_task_result(
    conn: &Connection,
    task_id: &str,
    result_json: &str,
    now: &str,
) -> Result<(), DbError> {
    conn.execute(
        "UPDATE tasks SET result_json = ?1, updated_at = ?2 WHERE id = ?3",
        params![result_json, now, task_id],
    )?;
    Ok(())
}

/// Increment retry count and set error info.
pub fn increment_retry(
    conn: &Connection,
    task_id: &str,
    error_code: &str,
    error_message: &str,
    now: &str,
) -> Result<(), DbError> {
    conn.execute(
        "UPDATE tasks SET retry_count = retry_count + 1, error_code = ?1,
         error_message = ?2, updated_at = ?3 WHERE id = ?4",
        params![error_code, error_message, now, task_id],
    )?;
    Ok(())
}

/// Mark all non-succeeded tasks for a job as cancelled.
pub fn cancel_all_non_succeeded(
    conn: &Connection,
    job_id: &str,
    now: &str,
) -> Result<usize, DbError> {
    let changed = conn.execute(
        "UPDATE tasks SET status = 'cancelled', cancel_requested = 1,
         finished_at = ?1, updated_at = ?1
         WHERE job_id = ?2 AND status NOT IN ('succeeded', 'cancelled')",
        params![now, job_id],
    )?;
    Ok(changed)
}

/// Resume: transition RUNNING -> QUEUED for crash recovery.
pub fn resume_running_tasks(conn: &Connection, job_id: &str, now: &str) -> Result<usize, DbError> {
    let changed = conn.execute(
        "UPDATE tasks SET status = 'queued', updated_at = ?1
         WHERE job_id = ?2 AND status = 'running'",
        params![now, job_id],
    )?;
    Ok(changed)
}

/// Check if all tasks for a job are in terminal states.
pub fn all_tasks_terminal(conn: &Connection, job_id: &str) -> Result<bool, DbError> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM tasks WHERE job_id = ?1 AND status NOT IN
         ('succeeded', 'failed', 'cancelled')",
        params![job_id],
        |row| row.get(0),
    )?;
    Ok(count == 0)
}

/// Check if any task for a job has failed permanently.
pub fn any_task_failed(conn: &Connection, job_id: &str) -> Result<bool, DbError> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM tasks WHERE job_id = ?1 AND status = 'failed'",
        params![job_id],
        |row| row.get(0),
    )?;
    Ok(count > 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mem_conn() -> Connection {
        Connection::open_in_memory().expect("in-memory sqlite")
    }

    fn setup_db(conn: &Connection) {
        conn.execute_batch(
            "CREATE TABLE jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                progress REAL DEFAULT 0.0, stage TEXT DEFAULT '',
                error_code TEXT, error_message TEXT, error_log TEXT,
                params_json TEXT, retry_count INTEGER DEFAULT 0,
                cancel_requested INTEGER DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                task_type TEXT NOT NULL, stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL DEFAULT 0.0,
                depends_on TEXT DEFAULT '[]',
                params_json TEXT, input_fingerprint TEXT, result_json TEXT,
                error_code TEXT, error_message TEXT,
                retry_count INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 3,
                cancel_requested INTEGER DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT
            );",
        )
        .expect("setup tables");
    }

    fn make_task(id: &str, job_id: &str) -> Task {
        Task {
            id: id.to_string(),
            job_id: job_id.to_string(),
            task_type: TaskType::Transcribe,
            stage: "transcribe".to_string(),
            status: TaskStatus::Queued,
            progress: 0.0,
            depends_on: "[]".to_string(),
            params_json: None,
            input_fingerprint: None,
            result_json: None,
            error_code: None,
            error_message: None,
            retry_count: 0,
            max_attempts: 3,
            cancel_requested: false,
            created_at: "2026-08-22T00:00:00Z".to_string(),
            updated_at: "2026-08-22T00:00:00Z".to_string(),
            started_at: None,
            finished_at: None,
        }
    }

    #[test]
    fn task_crud_roundtrip() {
        let conn = mem_conn();
        setup_db(&conn);

        // Insert job
        conn.execute(
            "INSERT INTO jobs (id, project_id, type, status, created_at, updated_at)
             VALUES ('job_1', 'proj_1', 'transcribe', 'queued', 't0', 't0')",
            [],
        )
        .expect("insert job");

        let task = make_task("task_1", "job_1");
        create_task(&conn, &task).expect("create task");

        let loaded = get_task(&conn, "task_1")
            .expect("get task")
            .expect("task exists");
        assert_eq!(loaded.id, "task_1");
        assert_eq!(loaded.status, TaskStatus::Queued);

        let tasks = get_tasks_by_job(&conn, "job_1").expect("get tasks");
        assert_eq!(tasks.len(), 1);
    }

    #[test]
    fn transition_guard() {
        let conn = mem_conn();
        setup_db(&conn);

        conn.execute(
            "INSERT INTO jobs (id, project_id, type, status, created_at, updated_at)
             VALUES ('job_1', 'proj_1', 'transcribe', 'queued', 't0', 't0')",
            [],
        )
        .expect("insert job");

        let task = make_task("task_1", "job_1");
        create_task(&conn, &task).expect("create task");

        // Queued -> Ready
        assert!(update_task_status(&conn, "task_1", TaskStatus::Ready, "t1").expect("ok"));
        // Ready -> Running
        assert!(update_task_status(&conn, "task_1", TaskStatus::Running, "t2").expect("ok"));
        // Running -> Succeeded
        assert!(update_task_status(&conn, "task_1", TaskStatus::Succeeded, "t3").expect("ok"));
        // Succeeded -> Failed (should fail, terminal)
        assert!(!update_task_status(&conn, "task_1", TaskStatus::Failed, "t4").expect("ok"));
    }

    #[test]
    fn cancel_all_non_succeeded_works() {
        let conn = mem_conn();
        setup_db(&conn);

        conn.execute(
            "INSERT INTO jobs (id, project_id, type, status, created_at, updated_at)
             VALUES ('job_1', 'proj_1', 'transcribe', 'running', 't0', 't0')",
            [],
        )
        .expect("insert job");

        let mut t1 = make_task("task_1", "job_1");
        t1.status = TaskStatus::Succeeded;
        create_task(&conn, &t1).expect("create t1");

        let t2 = make_task("task_2", "job_1");
        create_task(&conn, &t2).expect("create t2");

        let cancelled = cancel_all_non_succeeded(&conn, "job_1", "t9").expect("cancel");
        assert_eq!(cancelled, 1); // only t2 cancelled

        let t1_loaded = get_task(&conn, "task_1").unwrap().unwrap();
        assert_eq!(t1_loaded.status, TaskStatus::Succeeded);

        let t2_loaded = get_task(&conn, "task_2").unwrap().unwrap();
        assert_eq!(t2_loaded.status, TaskStatus::Cancelled);
    }
}
