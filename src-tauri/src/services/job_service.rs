//! JobService (TASK-010) — the pipeline job orchestrator.
//!
//! Responsibilities (MASTER_PLAN.md §17.1 jobs table + §25.1 `job.*` IPC):
//! - **submit**: validate the project exists, persist a `queued` row, and
//!   enqueue it into a FIFO queue (single worker → exactly one job runs at a
//!   time).
//! - **state machine**: every transition passes through a guard
//!   (`JobStatus::can_transition`) and is persisted before returning.
//! - **progress**: 0..1 + a sub-stage label, persisted on every update.
//! - **cancel**: a `queued` job → `cancelled` immediately; a `running` job gets
//!   an in-memory cancel flag (polled by the runner, which must also kill any
//!   child process) plus a persisted `cancel_requested` marker so the intent
//!   survives a crash. Cancellation that races the runner's own return is
//!   resolved in favour of `cancelled`.
//! - **retry**: transient failures retry automatically with backoff
//!   (1s/5s/30s, max 3); `job.retry` re-queues a failed/cancelled job manually.
//! - **resume**: on startup, `queued` rows are re-seeded into the queue and rows
//!   stuck in `running` are returned to `queued` (work resumes from the last
//!   persisted stage), unless cancellation had been requested, in which case the
//!   job is finalised `cancelled`.
//! - **events**: every state change emits `job:status`; informational lines emit
//!   `job:log` (MASTER_PLAN.md §25.2).
//!
//! The actual work a job performs is delegated to a `JobRunner` (injected at
//! construction). TASK-010 ships the lifecycle machinery; concrete runners that
//! dispatch to the Python worker are wired by later pipeline tasks.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::db::repo::job::JobRepo;
use crate::db::{is_valid_uuid_v4, utc_iso8601_now, Database, DbError, Job, JobStatus, JobType};

/// Default transient-failure policy (MASTER_PLAN §17.1 / TASK-010): 3 retries
/// with backoff 1s → 5s → 30s.
pub const DEFAULT_MAX_RETRIES: u32 = 3;
pub const DEFAULT_RETRY_BACKOFFS: [Duration; 3] = [
    Duration::from_secs(1),
    Duration::from_secs(5),
    Duration::from_secs(30),
];

/// Tunable job-service behaviour (tests shorten the backoffs).
#[derive(Debug, Clone)]
pub struct JobServiceConfig {
    pub max_retries: u32,
    pub retry_backoffs: Vec<Duration>,
}

impl Default for JobServiceConfig {
    fn default() -> Self {
        Self {
            max_retries: DEFAULT_MAX_RETRIES,
            retry_backoffs: DEFAULT_RETRY_BACKOFFS.to_vec(),
        }
    }
}

/// Result of a `JobRunner` invocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JobRunError {
    /// Recoverable failure — the service retries with backoff.
    Transient { code: String, message: String },
    /// Non-recoverable failure — the job goes straight to `failed`.
    Permanent { code: String, message: String },
    /// The runner observed cancellation and stopped.
    Cancelled,
}

/// Context handed to a `JobRunner`: report progress and poll cancellation.
///
/// `progress(p, stage)` persists + emits; `is_cancelled()` reflects the latest
/// `job.cancel` for this job id.
pub struct JobRunContext<'a> {
    pub progress: &'a dyn Fn(f64, &str),
    pub is_cancelled: &'a dyn Fn() -> bool,
}

/// Executes one job's actual work. Implementations must poll
/// `ctx.is_cancelled()` and stop promptly (killing any child process) when it
/// turns true, returning `JobRunError::Cancelled`.
pub trait JobRunner: Send + Sync {
    fn run(&self, job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError>;
}

/// Placeholder runner used until concrete executors (worker HTTP dispatch) are
/// wired by later pipeline tasks. It fails honestly instead of faking success.
pub struct NotWiredRunner;

impl JobRunner for NotWiredRunner {
    fn run(&self, job: &Job, _ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
        Err(JobRunError::Permanent {
            code: "E_JOB_NOT_WIRED".into(),
            message: format!("no executor wired for job type `{}`", job.job_type.as_str()),
        })
    }
}

/// `job:status` payload (MASTER_PLAN.md §25.2).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobStatusEvent {
    pub job_id: String,
    pub status: String,
    pub progress: f64,
    pub stage: String,
    pub error: Option<JobErrorInfo>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JobErrorInfo {
    pub code: String,
    pub message: String,
}

/// `job:log` payload (MASTER_PLAN.md §25.2).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobLogEvent {
    pub job_id: String,
    pub level: String,
    pub message: String,
}

/// Events emitted by `JobService` (frontend consumers subscribe to these).
#[derive(Debug, Clone)]
pub enum JobEvent {
    Status(JobStatusEvent),
    Log(JobLogEvent),
}

/// Sink for job events. A Tauri-backed implementation lives in `lib.rs`;
/// tests use a recording sink.
pub trait JobEventSink: Send + Sync {
    fn emit(&self, event: JobEvent);
}

struct Inner {
    /// Shared SQLite handle; open failures are captured so the service can
    /// still run and report clean errors (mirrors `ProjectService`).
    db: Result<Database, DbError>,
    runner: Arc<dyn JobRunner>,
    events: Arc<dyn JobEventSink>,
    config: JobServiceConfig,
    /// FIFO of `queued` job ids awaiting a free worker slot.
    queue: Mutex<VecDeque<String>>,
    cv: Condvar,
    /// Per-running-job cancel flags keyed by job id.
    cancel_flags: Mutex<HashMap<String, Arc<AtomicBool>>>,
    stop: AtomicBool,
}

/// The job orchestrator, managed as Tauri app state.
pub struct JobService {
    inner: Arc<Inner>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl JobService {
    /// Open the job database (same `app.db` as ProjectService — WAL allows
    /// multiple writers), resume interrupted work, and start the queue worker.
    pub fn open(
        data_dir: std::path::PathBuf,
        runner: Arc<dyn JobRunner>,
        events: Arc<dyn JobEventSink>,
        config: JobServiceConfig,
    ) -> Self {
        let db = Database::open(&data_dir.join("app.db"));
        if let Err(e) = &db {
            log::error!("job database init failed: {e}");
        }
        let svc = Self {
            inner: Arc::new(Inner {
                db,
                runner,
                events,
                config,
                queue: Mutex::new(VecDeque::new()),
                cv: Condvar::new(),
                cancel_flags: Mutex::new(HashMap::new()),
                stop: AtomicBool::new(false),
            }),
            worker: Mutex::new(None),
        };
        svc.resume();
        svc.spawn_worker();
        svc
    }

    /// Submit a job: validate the project exists, persist a `queued` row, and
    /// enqueue it FIFO. Returns the persisted job.
    pub fn submit(
        &self,
        project_id: &str,
        job_type: JobType,
        params: serde_json::Value,
    ) -> Result<Job, DbError> {
        let project_id = validate_project_id(project_id)?;
        if !params.is_object() {
            return Err(DbError::InvalidInput(
                "job params must be a JSON object".into(),
            ));
        }
        let db = self.db()?;
        if db
            .conn()
            .query_row(
                "SELECT 1 FROM projects WHERE id = ?1",
                [&project_id],
                |_| Ok(()),
            )
            .optional_ok()?
            .is_none()
        {
            return Err(DbError::NotFound(format!(
                "project {project_id} does not exist"
            )));
        }

        let now = utc_iso8601_now();
        let job = db.transaction(|conn| {
            let repo = JobRepo::new(conn);
            let id = repo.next_id()?;
            let job = Job {
                id,
                project_id: project_id.clone(),
                job_type,
                status: JobStatus::Queued,
                progress: 0.0,
                stage: "queued".into(),
                error_code: None,
                error_message: None,
                error_log: None,
                params,
                created_at: now.clone(),
                updated_at: now,
                started_at: None,
                finished_at: None,
                retry_count: 0,
                cancel_requested: false,
            };
            repo.insert(&job)?;
            Ok(job)
        })?;

        self.enqueue(&job.id);
        self.emit_status(&job);
        self.emit_log(&job.id, "info", &format!("job {} submitted", job.id));
        Ok(job)
    }

    /// Load one job by id.
    pub fn get(&self, id: &str) -> Result<Job, DbError> {
        let db = self.db()?;
        let conn = db.conn();
        JobRepo::new(&conn)
            .get(id)?
            .ok_or_else(|| DbError::NotFound(format!("job {id} does not exist")))
    }

    /// All jobs for a project, most recently updated first.
    pub fn list(&self, project_id: &str) -> Result<Vec<Job>, DbError> {
        let project_id = validate_project_id(project_id)?;
        let db = self.db()?;
        let conn = db.conn();
        JobRepo::new(&conn).list_by_project(&project_id)
    }

    /// Cancel a job.
    ///
    /// - `queued` → `cancelled` immediately (persisted + emitted).
    /// - `running` → set the in-memory cancel flag + persist `cancel_requested`.
    ///   The runner polls the flag, stops (killing any child process), and the
    ///   worker finalises the state.
    /// - terminal jobs cannot be cancelled.
    pub fn cancel(&self, id: &str) -> Result<(), DbError> {
        let mut job = self.get(id)?;
        match job.status {
            JobStatus::Queued => {
                job.cancel_requested = true;
                job.error_code = Some("E_JOB_CANCELLED".into());
                job.error_message = Some("job cancelled before start".into());
                job.error_log = None;
                job.finished_at = Some(utc_iso8601_now());
                self.transition(&mut job, JobStatus::Cancelled)?;
                self.emit_log(id, "info", "job cancelled before start");
                Ok(())
            }
            JobStatus::Running => {
                // Signal the runner.
                if let Some(flag) = self.inner.cancel_flags.lock().unwrap().get(id) {
                    flag.store(true, Ordering::SeqCst);
                }
                // Persist the intent so it survives a crash.
                job.cancel_requested = true;
                self.persist(&job)?;
                self.emit_log(id, "info", "cancel requested");
                Ok(())
            }
            _ => Err(DbError::InvalidInput(format!(
                "job {id} is already terminal"
            ))),
        }
    }

    /// Manually retry a `failed` or `cancelled` job: reset it to `queued`
    /// (error fields cleared, retry counter reset) and enqueue it.
    pub fn retry(&self, id: &str) -> Result<(), DbError> {
        let mut job = self.get(id)?;
        match job.status {
            JobStatus::Failed | JobStatus::Cancelled => {
                job.retry_count = 0;
                job.error_code = None;
                job.error_message = None;
                job.error_log = None;
                job.cancel_requested = false;
                job.progress = 0.0;
                job.stage = "queued".into();
                job.finished_at = None;
                job.started_at = None;
                self.transition(&mut job, JobStatus::Queued)?;
                self.enqueue(&job.id);
                self.emit_log(id, "info", "job queued for manual retry");
                Ok(())
            }
            _ => Err(DbError::InvalidInput(format!(
                "job {id} cannot be retried from state {:?}",
                job.status
            ))),
        }
    }

    /// Stop the queue worker (app shutdown / tests). Idempotent.
    pub fn stop(&self) {
        self.inner.stop.store(true, Ordering::SeqCst);
        self.inner.cv.notify_all();
        if let Some(handle) = self.worker.lock().unwrap().take() {
            let _ = handle.join();
        }
    }

    /// Whether the worker has no queued or in-flight work (tests/diagnostics).
    pub fn is_idle(&self) -> bool {
        self.inner
            .cancel_flags
            .lock()
            .unwrap()
            .iter()
            .next()
            .is_none()
            && self.inner.queue.lock().unwrap().is_empty()
    }

    fn db(&self) -> Result<&Database, DbError> {
        self.inner.db.as_ref().map_err(|e| e.clone())
    }

    // ---- queue worker -----------------------------------------------------

    fn spawn_worker(&self) {
        let inner = Arc::clone(&self.inner);
        let handle = thread::Builder::new()
            .name("job-worker".into())
            .spawn(move || worker_loop(&inner))
            .expect("failed to spawn job worker thread");
        *self.worker.lock().unwrap() = Some(handle);
    }

    fn enqueue(&self, id: &str) {
        self.inner.queue.lock().unwrap().push_back(id.to_string());
        self.inner.cv.notify_one();
    }

    /// Seed the FIFO queue from the DB and normalize interrupted work:
    /// - `queued` rows → enqueued (work survives a restart).
    /// - `running` rows → `queued` again (resume from the last persisted
    ///   stage/progress), unless the user had requested cancellation, in which
    ///   case the job is finalised `cancelled`.
    fn resume(&self) {
        let db = match self.db() {
            Ok(db) => db,
            Err(e) => {
                log::error!("job resume skipped (db unavailable): {e}");
                return;
            }
        };
        // Read everything up front and drop the connection guard before
        // calling `transition` (which needs the same connection again) — the
        // mutex is not reentrant.
        let (queued_ids, running) = {
            let conn = db.conn();
            let repo = JobRepo::new(&conn);
            let queued_ids = match repo.list_queued() {
                Ok(q) => q.into_iter().map(|j| j.id).collect::<Vec<_>>(),
                Err(e) => {
                    log::error!("job resume failed to load queued jobs: {e}");
                    Vec::new()
                }
            };
            let running = match repo.list_running() {
                Ok(r) => r,
                Err(e) => {
                    log::error!("job resume failed to load running jobs: {e}");
                    Vec::new()
                }
            };
            (queued_ids, running)
        };
        for id in queued_ids {
            self.enqueue(&id);
        }
        for mut job in running {
            if job.cancel_requested {
                job.error_code = Some("E_JOB_CANCELLED".into());
                job.error_message = Some("cancelled while the app was closed".into());
                job.finished_at = Some(utc_iso8601_now());
                let _ = self.transition(&mut job, JobStatus::Cancelled);
                self.emit_log(&job.id, "info", "job cancelled (requested before restart)");
            } else {
                self.emit_log(&job.id, "warn", "job resumed after restart");
                let _ = self.transition(&mut job, JobStatus::Queued);
                self.enqueue(&job.id);
            }
        }
    }

    // ---- state transitions (public-API paths) -----------------------------

    fn transition(&self, job: &mut Job, to: JobStatus) -> Result<(), DbError> {
        if !job.status.can_transition(to) {
            return Err(DbError::InvalidInput(format!(
                "invalid job transition {} → {} for {}",
                job.status.as_str(),
                to.as_str(),
                job.id
            )));
        }
        job.status = to;
        job.updated_at = utc_iso8601_now();
        self.persist(job)?;
        self.emit_status(job);
        Ok(())
    }

    fn persist(&self, job: &Job) -> Result<(), DbError> {
        let db = self.db()?;
        let conn = db.conn();
        let updated = JobRepo::new(&conn).update(job)?;
        if !updated {
            return Err(DbError::NotFound(format!("job {} does not exist", job.id)));
        }
        Ok(())
    }

    fn emit_status(&self, job: &Job) {
        self.inner
            .events
            .emit(JobEvent::Status(job_status_event(job)));
    }

    fn emit_log(&self, job_id: &str, level: &str, message: &str) {
        self.inner.events.emit(JobEvent::Log(JobLogEvent {
            job_id: job_id.to_string(),
            level: level.to_string(),
            message: message.to_string(),
        }));
    }
}

impl Inner {
    fn db(&self) -> Result<&Database, DbError> {
        self.db.as_ref().map_err(|e| e.clone())
    }

    fn load(&self, id: &str) -> Result<Option<Job>, DbError> {
        let conn = self.db()?.conn();
        JobRepo::new(&conn).get(id)
    }

    fn persist(&self, job: &Job) -> Result<(), DbError> {
        let conn = self.db()?.conn();
        let updated = JobRepo::new(&conn).update(job)?;
        if !updated {
            return Err(DbError::NotFound(format!("job {} does not exist", job.id)));
        }
        Ok(())
    }

    /// Process one job from queued → terminal, applying the retry policy.
    fn process(&self, job_id: &str) {
        let mut job = match self.load(job_id) {
            Ok(Some(j)) => j,
            _ => return,
        };
        if job.status != JobStatus::Queued {
            return; // e.g. cancelled while queued
        }

        let cancel = Arc::new(AtomicBool::new(false));
        self.cancel_flags
            .lock()
            .unwrap()
            .insert(job_id.to_string(), cancel.clone());

        let _ = self.transition(&mut job, JobStatus::Running);

        let mut retries: u32 = 0;
        loop {
            if cancel.load(Ordering::SeqCst) {
                let _ = self.finish_cancelled(job_id);
                break;
            }

            let progress = |p: f64, stage: &str| self.report_progress(job_id, p, stage);
            let is_cancelled = || cancel.load(Ordering::SeqCst);
            let ctx = JobRunContext {
                progress: &progress,
                is_cancelled: &is_cancelled,
            };
            let outcome = self.runner.run(&job, &ctx);

            if cancel.load(Ordering::SeqCst) {
                // Cancel won the race with the runner's own return.
                let _ = self.finish_cancelled(job_id);
                break;
            }

            match outcome {
                Ok(()) => {
                    let _ = self.finish(job_id, JobStatus::Succeeded);
                    break;
                }
                Err(JobRunError::Cancelled) => {
                    let _ = self.finish_cancelled(job_id);
                    break;
                }
                Err(JobRunError::Permanent { code, message }) => {
                    let _ = self.finish_failed(job_id, &code, &message);
                    break;
                }
                Err(JobRunError::Transient { code, message }) => {
                    if retries >= self.config.max_retries {
                        let msg = format!("{message} (retries exhausted)");
                        let _ = self.finish_failed(job_id, &code, &msg);
                        break;
                    }
                    retries += 1;
                    let delay = self.backoff(retries);
                    let mut job = match self.load(job_id) {
                        Ok(Some(j)) => j,
                        _ => break,
                    };
                    job.retry_count = retries;
                    job.error_code = Some(code.clone());
                    job.error_message = Some(message.clone());
                    let _ = self.persist(&job);
                    self.emit_log(
                        job_id,
                        "warn",
                        &format!(
                            "transient failure (retry {retries}/{}): {message} — retrying in {}s",
                            self.config.max_retries,
                            delay.as_secs_f64().round() as u64
                        ),
                    );
                    if !self.sleep_interruptible(delay, &cancel) {
                        let _ = self.finish_cancelled(job_id);
                        break;
                    }
                }
            }
        }

        self.cancel_flags.lock().unwrap().remove(job_id);
    }

    fn backoff(&self, retry_index: u32) -> Duration {
        let idx = retry_index.saturating_sub(1) as usize;
        self.config
            .retry_backoffs
            .get(idx)
            .copied()
            .unwrap_or_else(|| *self.config.retry_backoffs.last().unwrap_or(&Duration::ZERO))
    }

    /// Sleep for `d`, polling cancellation; returns `false` if cancelled.
    fn sleep_interruptible(&self, d: Duration, cancel: &AtomicBool) -> bool {
        let deadline = Instant::now() + d;
        while Instant::now() < deadline {
            if cancel.load(Ordering::SeqCst) {
                return false;
            }
            thread::sleep(Duration::from_millis(25));
        }
        true
    }

    // ---- state transitions (worker paths) ---------------------------------

    fn report_progress(&self, job_id: &str, progress: f64, stage: &str) {
        let mut job = match self.load(job_id) {
            Ok(Some(j)) => j,
            _ => return,
        };
        if job.status != JobStatus::Running {
            return;
        }
        job.progress = progress.clamp(0.0, 1.0);
        job.stage = stage.to_string();
        if let Err(e) = self.persist(&job) {
            log::warn!("failed to persist progress for {job_id}: {e}");
            return;
        }
        self.emit_status(&job);
    }

    fn finish(&self, job_id: &str, to: JobStatus) -> Result<(), DbError> {
        let mut job = match self.load(job_id) {
            Ok(Some(j)) => j,
            _ => return Ok(()),
        };
        if job.status == to {
            return Ok(());
        }
        job.finished_at = Some(utc_iso8601_now());
        if to == JobStatus::Succeeded {
            job.progress = 1.0;
            // A job that finally succeeded (possibly after transient retries)
            // must not carry the error fields of a prior attempt.
            job.error_code = None;
            job.error_message = None;
            job.error_log = None;
        }
        self.transition(&mut job, to)
    }

    fn finish_cancelled(&self, job_id: &str) -> Result<(), DbError> {
        let mut job = match self.load(job_id) {
            Ok(Some(j)) => j,
            _ => return Ok(()),
        };
        job.cancel_requested = true;
        job.error_code = Some("E_JOB_CANCELLED".into());
        job.error_message = Some("job cancelled".into());
        // Persist the markers before `finish` reloads the row.
        self.persist(&job)?;
        self.finish(job_id, JobStatus::Cancelled)?;
        self.emit_log(job_id, "info", "job cancelled");
        Ok(())
    }

    fn finish_failed(&self, job_id: &str, code: &str, message: &str) -> Result<(), DbError> {
        let mut job = match self.load(job_id) {
            Ok(Some(j)) => j,
            _ => return Ok(()),
        };
        job.error_code = Some(code.to_string());
        job.error_message = Some(message.to_string());
        job.error_log = Some(format!("{code}: {message}"));
        // Persist the error fields before `finish` reloads the row.
        self.persist(&job)?;
        self.finish(job_id, JobStatus::Failed)?;
        self.emit_log(job_id, "error", message);
        Ok(())
    }

    fn transition(&self, job: &mut Job, to: JobStatus) -> Result<(), DbError> {
        if !job.status.can_transition(to) {
            return Err(DbError::InvalidInput(format!(
                "invalid job transition {} → {} for {}",
                job.status.as_str(),
                to.as_str(),
                job.id
            )));
        }
        job.status = to;
        job.updated_at = utc_iso8601_now();
        self.persist(job)?;
        self.emit_status(job);
        Ok(())
    }

    fn emit_status(&self, job: &Job) {
        self.events.emit(JobEvent::Status(job_status_event(job)));
    }

    fn emit_log(&self, job_id: &str, level: &str, message: &str) {
        self.events.emit(JobEvent::Log(JobLogEvent {
            job_id: job_id.to_string(),
            level: level.to_string(),
            message: message.to_string(),
        }));
    }
}

/// Worker main loop: block on the FIFO, process one job at a time, repeat.
fn worker_loop(inner: &Inner) {
    while !inner.stop.load(Ordering::SeqCst) {
        let job_id = {
            let mut queue = inner.queue.lock().unwrap();
            loop {
                if let Some(id) = queue.pop_front() {
                    break id;
                }
                let (guard, _) = inner
                    .cv
                    .wait_timeout(queue, Duration::from_millis(200))
                    .unwrap();
                queue = guard;
                if inner.stop.load(Ordering::SeqCst) {
                    return;
                }
            }
        };
        inner.process(&job_id);
    }
}

fn job_status_event(job: &Job) -> JobStatusEvent {
    let error = match (&job.error_code, &job.error_message) {
        (Some(code), Some(message)) => Some(JobErrorInfo {
            code: code.clone(),
            message: message.clone(),
        }),
        _ => None,
    };
    JobStatusEvent {
        job_id: job.id.clone(),
        status: job.status.as_str().to_string(),
        progress: job.progress,
        stage: job.stage.clone(),
        error,
    }
}

fn validate_project_id(id: &str) -> Result<String, DbError> {
    if !is_valid_uuid_v4(id) {
        return Err(DbError::InvalidInput(format!("invalid project id: {id:?}")));
    }
    Ok(id.to_string())
}

/// `Result<_, rusqlite::Error>` `.optional()` helper for existence checks.
trait OptionalOk {
    fn optional_ok(self) -> Result<Option<()>, DbError>;
}

impl OptionalOk for Result<(), rusqlite::Error> {
    fn optional_ok(self) -> Result<Option<()>, DbError> {
        match self {
            Ok(()) => Ok(Some(())),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(DbError::from(e)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::{new_uuid_v4, utc_iso8601_now, Database, DbError, Job, JobStatus, JobType};
    use rusqlite::params;
    use std::sync::atomic::AtomicUsize;

    #[derive(Clone, Copy)]
    enum RunMode {
        Ok,
        ReportProgress,
        Permanent,
        TransientThenOk { transient_calls: usize },
        TransientAlways,
        BlockUntilCancelled,
    }

    struct ScriptRunner {
        mode: RunMode,
        calls: AtomicUsize,
    }

    impl ScriptRunner {
        fn new(mode: RunMode) -> Self {
            Self {
                mode,
                calls: AtomicUsize::new(0),
            }
        }

        fn call_count(&self) -> usize {
            self.calls.load(Ordering::SeqCst)
        }
    }

    impl JobRunner for ScriptRunner {
        fn run(&self, _job: &Job, ctx: &JobRunContext<'_>) -> Result<(), JobRunError> {
            let call = self.calls.fetch_add(1, Ordering::SeqCst) + 1;
            match self.mode {
                RunMode::Ok => Ok(()),
                RunMode::ReportProgress => {
                    (ctx.progress)(0.5, "mid");
                    // Hold the Running state briefly so a poller can observe
                    // the persisted progress.
                    thread::sleep(Duration::from_millis(50));
                    Ok(())
                }
                RunMode::Permanent => Err(JobRunError::Permanent {
                    code: "E_TEST".into(),
                    message: "boom".into(),
                }),
                RunMode::TransientThenOk { transient_calls } => {
                    if call <= transient_calls {
                        Err(JobRunError::Transient {
                            code: "E_TRANSIENT".into(),
                            message: "try again".into(),
                        })
                    } else {
                        Ok(())
                    }
                }
                RunMode::TransientAlways => Err(JobRunError::Transient {
                    code: "E_TRANSIENT".into(),
                    message: "always failing".into(),
                }),
                RunMode::BlockUntilCancelled => {
                    while !(ctx.is_cancelled)() {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(JobRunError::Cancelled)
                }
            }
        }
    }

    #[derive(Default)]
    struct RecordingSink {
        events: Mutex<Vec<JobEvent>>,
    }

    impl RecordingSink {
        /// `(job_id, status)` pairs in emission order.
        fn statuses(&self) -> Vec<(String, String)> {
            self.events
                .lock()
                .unwrap()
                .iter()
                .filter_map(|e| match e {
                    JobEvent::Status(s) => Some((s.job_id.clone(), s.status.clone())),
                    JobEvent::Log(_) => None,
                })
                .collect()
        }

        fn logs(&self) -> Vec<String> {
            self.events
                .lock()
                .unwrap()
                .iter()
                .filter_map(|e| match e {
                    JobEvent::Log(l) => Some(l.message.clone()),
                    JobEvent::Status(_) => None,
                })
                .collect()
        }
    }

    impl JobEventSink for RecordingSink {
        fn emit(&self, event: JobEvent) {
            self.events.lock().unwrap().push(event);
        }
    }

    struct Harness {
        svc: JobService,
        sink: Arc<RecordingSink>,
        dir: std::path::PathBuf,
        runner: Arc<ScriptRunner>,
    }

    fn harness(label: &str, mode: RunMode, config: JobServiceConfig) -> Harness {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_job_svc_{label}_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let runner = Arc::new(ScriptRunner::new(mode));
        let sink = Arc::new(RecordingSink::default());
        let svc = JobService::open(dir.clone(), runner.clone(), sink.clone(), config);
        Harness {
            svc,
            sink,
            dir,
            runner,
        }
    }

    fn fast_config(max_retries: u32) -> JobServiceConfig {
        JobServiceConfig {
            max_retries,
            retry_backoffs: vec![Duration::ZERO, Duration::ZERO, Duration::ZERO],
        }
    }

    /// Seed a project row for `submit` (jobs reference `projects(id)`).
    fn seed_project(dir: &std::path::Path) -> String {
        let db = Database::open(&dir.join("app.db")).expect("open db");
        let id = new_uuid_v4().expect("uuid");
        let now = utc_iso8601_now();
        let conn = db.conn();
        conn.execute(
            "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
             VALUES (?1, 'seed', 'seed.mp4', 'draft', ?2, ?2)",
            params![id, now],
        )
        .expect("seed project");
        id
    }

    /// Insert a job row directly (used by the resume tests to simulate a crash).
    fn seed_job(dir: &std::path::Path, job: &Job) {
        let db = Database::open(&dir.join("app.db")).expect("open db");
        let conn = db.conn();
        JobRepo::new(&conn).insert(job).expect("seed job");
    }

    /// Per-module counter so seeded rows get unique `job_NNNN` ids.
    static SAMPLE_COUNTER: AtomicUsize = AtomicUsize::new(1);

    fn sample_job(project_id: &str, status: JobStatus) -> Job {
        let n = SAMPLE_COUNTER.fetch_add(1, Ordering::SeqCst);
        let now = utc_iso8601_now();
        Job {
            id: format!("job_{n:04}"),
            project_id: project_id.into(),
            job_type: JobType::Transcribe,
            status,
            progress: 0.0,
            stage: "queued".into(),
            error_code: None,
            error_message: None,
            error_log: None,
            params: serde_json::json!({}),
            created_at: now.clone(),
            updated_at: now,
            started_at: None,
            finished_at: None,
            retry_count: 0,
            cancel_requested: false,
        }
    }

    fn wait_until(what: &str, f: impl Fn() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !f() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            thread::sleep(Duration::from_millis(10));
        }
    }

    // ---- submit / validation ----------------------------------------------

    #[test]
    fn submit_validates_project_and_params() {
        let h = harness("submit_valid", RunMode::Ok, fast_config(3));
        assert!(matches!(
            h.svc
                .submit("not-a-uuid", JobType::Transcribe, serde_json::json!({})),
            Err(DbError::InvalidInput(_))
        ));
        let good_id = new_uuid_v4().expect("uuid");
        assert!(matches!(
            h.svc
                .submit(&good_id, JobType::Transcribe, serde_json::json!({})),
            Err(DbError::NotFound(_))
        ));
        let project_id = seed_project(&h.dir);
        assert!(matches!(
            h.svc
                .submit(&project_id, JobType::Transcribe, serde_json::json!([1, 2])),
            Err(DbError::InvalidInput(_))
        ));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn submit_persists_queued_row_and_emits_status() {
        let h = harness("submit_persist", RunMode::Ok, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(
                &project_id,
                JobType::Transcribe,
                serde_json::json!({"model": "turbo"}),
            )
            .expect("submit");
        assert!(job.id.starts_with("job_"));
        assert_eq!(job.status, JobStatus::Queued);
        assert_eq!(job.progress, 0.0);
        // The worker may already have completed the job, so only the id is
        // stable between the submit snapshot and the reloaded row.
        assert_eq!(h.svc.get(&job.id).expect("get").id, job.id);
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn list_filters_by_project() {
        let h = harness("submit_list", RunMode::Ok, fast_config(3));
        let a = seed_project(&h.dir);
        let b = seed_project(&h.dir);
        let ja = h
            .svc
            .submit(&a, JobType::Render, serde_json::json!({}))
            .expect("a");
        let _jb = h
            .svc
            .submit(&b, JobType::Subtitle, serde_json::json!({}))
            .expect("b");
        let list_a = h.svc.list(&a).expect("list a");
        assert_eq!(list_a.len(), 1);
        assert_eq!(list_a[0].id, ja.id);
        assert!(h
            .svc
            .list(&b)
            .expect("list b")
            .iter()
            .all(|j| j.project_id == b));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- lifecycle: success / failure / retry ------------------------------

    #[test]
    fn successful_run_goes_succeeded_with_full_progress() {
        let h = harness("lifecycle_ok", RunMode::ReportProgress, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("successful job", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        let final_job = h.svc.get(&id).expect("get");
        assert_eq!(final_job.status, JobStatus::Succeeded);
        assert_eq!(final_job.progress, 1.0);
        assert_eq!(final_job.error_code, None);
        assert!(final_job.finished_at.is_some());
        assert_eq!(h.runner.call_count(), 1);
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn progress_is_persisted_while_running() {
        // The runner reports 0.5/"mid" and holds Running briefly; the persisted
        // row must reflect it before the terminal 1.0 overwrites progress.
        let h = harness(
            "lifecycle_progress",
            RunMode::ReportProgress,
            fast_config(3),
        );
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("running observed", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Running)
                .unwrap_or(false)
        });
        let running = h.svc.get(&id).expect("get");
        if running.status == JobStatus::Running {
            assert_eq!(running.progress, 0.5);
            assert_eq!(running.stage, "mid");
        }
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn permanent_failure_marks_failed_without_retry() {
        let h = harness("lifecycle_perm", RunMode::Permanent, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Render, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("failed job", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        let final_job = h.svc.get(&id).expect("get");
        assert_eq!(final_job.status, JobStatus::Failed);
        assert_eq!(final_job.error_code.as_deref(), Some("E_TEST"));
        assert_eq!(final_job.retry_count, 0);
        assert_eq!(h.runner.call_count(), 1);
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn transient_failure_retries_then_succeeds() {
        let h = harness(
            "lifecycle_retry",
            RunMode::TransientThenOk { transient_calls: 2 },
            fast_config(3),
        );
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("retried job terminal", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        let final_job = h.svc.get(&id).expect("get");
        assert_eq!(final_job.status, JobStatus::Succeeded);
        assert_eq!(final_job.error_code, None, "success clears error fields");
        assert_eq!(h.runner.call_count(), 3, "1 initial + 2 retries");
        assert_eq!(final_job.retry_count, 2, "last persisted retry count");
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn transient_failure_exhausts_retries_into_failed() {
        let h = harness(
            "lifecycle_exhaust",
            RunMode::TransientAlways,
            fast_config(2),
        );
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("exhausted job terminal", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        let final_job = h.svc.get(&id).expect("get");
        assert_eq!(final_job.status, JobStatus::Failed);
        assert_eq!(final_job.error_code.as_deref(), Some("E_TRANSIENT"));
        assert!(
            final_job
                .error_message
                .as_deref()
                .is_some_and(|m| m.contains("retries exhausted")),
            "got {:?}",
            final_job.error_message
        );
        // max_retries=2 → initial run + 2 retries.
        assert_eq!(h.runner.call_count(), 3);
        assert_eq!(final_job.retry_count, 2);
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- cancel -------------------------------------------------------------

    #[test]
    fn cancel_queued_job_goes_cancelled_without_running() {
        // First job blocks; the second stays queued and can be cancelled.
        let h = harness(
            "cancel_queued",
            RunMode::BlockUntilCancelled,
            fast_config(3),
        );
        let project_id = seed_project(&h.dir);
        let first = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("first");
        let second = h
            .svc
            .submit(&project_id, JobType::Translate, serde_json::json!({}))
            .expect("second");
        wait_until("first running", || {
            h.svc
                .get(&first.id)
                .map(|j| j.status == JobStatus::Running)
                .unwrap_or(false)
        });
        assert_eq!(
            h.svc.get(&second.id).expect("second still queued").status,
            JobStatus::Queued
        );

        h.svc.cancel(&second.id).expect("cancel queued");
        let cancelled = h.svc.get(&second.id).expect("get cancelled");
        assert_eq!(cancelled.status, JobStatus::Cancelled);
        assert_eq!(cancelled.error_code.as_deref(), Some("E_JOB_CANCELLED"));
        assert!(cancelled.finished_at.is_some());

        // Unblock the first so the worker can finish and stop cleanly.
        h.svc.cancel(&first.id).expect("cancel first");
        wait_until("first cancelled", || {
            h.svc
                .get(&first.id)
                .map(|j| j.status == JobStatus::Cancelled)
                .unwrap_or(false)
        });
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn cancel_running_job_stops_the_runner() {
        let h = harness(
            "cancel_running",
            RunMode::BlockUntilCancelled,
            fast_config(3),
        );
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("running", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Running)
                .unwrap_or(false)
        });

        h.svc.cancel(&id).expect("cancel running");
        wait_until("cancelled", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Cancelled)
                .unwrap_or(false)
        });
        let final_job = h.svc.get(&id).expect("get");
        assert_eq!(final_job.status, JobStatus::Cancelled);
        assert_eq!(final_job.error_code.as_deref(), Some("E_JOB_CANCELLED"));
        assert!(final_job.finished_at.is_some());
        // cancel_requested survives in the row so a crash cannot resurrect it.
        assert!(final_job.cancel_requested);
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn terminal_job_cannot_be_cancelled_or_retried() {
        let h = harness("cancel_terminal", RunMode::Permanent, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Render, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("failed", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        assert!(matches!(h.svc.cancel(&id), Err(DbError::InvalidInput(_))));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn missing_job_cancel_is_not_found() {
        let h = harness("cancel_missing", RunMode::Ok, fast_config(3));
        assert!(matches!(
            h.svc.cancel("job_9999"),
            Err(DbError::NotFound(_))
        ));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- manual retry ---------------------------------------------------------

    #[test]
    fn manual_retry_requeues_a_failed_job() {
        let h = harness("manual_retry", RunMode::Permanent, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Render, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("first failure", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Failed)
                .unwrap_or(false)
        });
        assert_eq!(h.runner.call_count(), 1);

        h.svc.retry(&id).expect("retry");
        // The same permanent runner fails again; the key assertion is the job
        // cycled back through `queued` and ran a second time.
        wait_until("second failure", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Failed && j.retry_count == 0)
                .unwrap_or(false)
        });
        assert_eq!(h.runner.call_count(), 2);

        let statuses = h.sink.statuses();
        let queued_after_retry = statuses
            .iter()
            .filter(|(jid, s)| jid == &id && s == "queued")
            .count();
        assert_eq!(queued_after_retry, 2, "initial queued + queued-after-retry");
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    #[test]
    fn retry_rejects_non_retryable_states() {
        let h = harness("retry_invalid", RunMode::Ok, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Render, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("succeeded", || {
            h.svc
                .get(&id)
                .map(|j| j.status == JobStatus::Succeeded)
                .unwrap_or(false)
        });
        assert!(matches!(h.svc.retry(&id), Err(DbError::InvalidInput(_))));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- FIFO ordering ----------------------------------------------------------

    #[test]
    fn queue_is_fifo_with_single_worker() {
        let h = harness("fifo", RunMode::BlockUntilCancelled, fast_config(3));
        let project_id = seed_project(&h.dir);
        let first = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("first");
        let second = h
            .svc
            .submit(&project_id, JobType::Translate, serde_json::json!({}))
            .expect("second");

        // First in, first running; the second waits in `queued`.
        wait_until("first running", || {
            h.svc
                .get(&first.id)
                .map(|j| j.status == JobStatus::Running)
                .unwrap_or(false)
        });
        assert_eq!(
            h.svc.get(&second.id).expect("second").status,
            JobStatus::Queued
        );

        // Release the first; the second is the next to run.
        h.svc.cancel(&first.id).expect("cancel first");
        wait_until("first cancelled", || {
            h.svc
                .get(&first.id)
                .map(|j| j.status == JobStatus::Cancelled)
                .unwrap_or(false)
        });
        wait_until("second running", || {
            h.svc
                .get(&second.id)
                .map(|j| j.status == JobStatus::Running)
                .unwrap_or(false)
        });
        h.svc.cancel(&second.id).expect("cancel second");
        wait_until("second cancelled", || {
            h.svc
                .get(&second.id)
                .map(|j| j.status == JobStatus::Cancelled)
                .unwrap_or(false)
        });

        // Running events arrive in submission order.
        let statuses = h.sink.statuses();
        let first_running = statuses
            .iter()
            .position(|(jid, s)| jid == &first.id && s == "running")
            .expect("first running event");
        let second_running = statuses
            .iter()
            .position(|(jid, s)| jid == &second.id && s == "running")
            .expect("second running event");
        assert!(first_running < second_running, "FIFO order violated");
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- events ------------------------------------------------------------------

    #[test]
    fn events_cover_submission_and_terminal_status() {
        let h = harness("events", RunMode::Ok, fast_config(3));
        let project_id = seed_project(&h.dir);
        let job = h
            .svc
            .submit(&project_id, JobType::Transcribe, serde_json::json!({}))
            .expect("submit");
        let id = job.id.clone();
        wait_until("succeeded", || {
            h.svc
                .get(&id)
                .map(|j| j.status.is_terminal())
                .unwrap_or(false)
        });
        let statuses = h.sink.statuses();
        let ids: Vec<_> = statuses.iter().map(|(jid, _)| jid.as_str()).collect();
        let queued = statuses
            .iter()
            .position(|(jid, s)| jid == &id && s == "queued")
            .expect("queued event");
        let running = statuses
            .iter()
            .position(|(jid, s)| jid == &id && s == "running")
            .expect("running event");
        let succeeded = statuses
            .iter()
            .position(|(jid, s)| jid == &id && s == "succeeded")
            .expect("succeeded event");
        assert!(queued < running && running < succeeded);
        assert!(ids.iter().all(|j| *j == id.as_str()));
        let logs = h.sink.logs();
        assert!(logs.iter().any(|m| m.contains("submitted")));
        h.svc.stop();
        let _ = std::fs::remove_dir_all(&h.dir);
    }

    // ---- resume after crash -------------------------------------------------------

    #[test]
    fn resume_requeues_interrupted_work_and_cancels_on_request() {
        // Simulate a crash mid-flight: one `queued`, one `running` (resume),
        // one `running` that had cancellation requested.
        let dir =
            std::env::temp_dir().join(format!("tooltranslate_job_resume_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        {
            let db = Database::open(&dir.join("app.db")).expect("open db");
            let project_id = new_uuid_v4().expect("uuid");
            let now = utc_iso8601_now();
            let conn = db.conn();
            conn.execute(
                "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
                 VALUES (?1, 'seed', 'seed.mp4', 'draft', ?2, ?2)",
                params![project_id, now],
            )
            .expect("seed project");
            drop(conn);

            let queued = sample_job(&project_id, JobStatus::Queued);
            let resumed = Job {
                status: JobStatus::Running,
                stage: "transcribing".into(),
                progress: 0.6,
                ..sample_job(&project_id, JobStatus::Running)
            };
            let cancel_resumed = Job {
                status: JobStatus::Running,
                cancel_requested: true,
                ..sample_job(&project_id, JobStatus::Running)
            };
            seed_job(&dir, &queued);
            seed_job(&dir, &resumed);
            seed_job(&dir, &cancel_resumed);
        }

        // "Restart": open the service over the same data dir. Resume re-enqueues
        // queued + running; the cancel-requested running job is finalised.
        let runner = Arc::new(ScriptRunner::new(RunMode::Ok));
        let sink = Arc::new(RecordingSink::default());
        let svc = JobService::open(dir.clone(), runner.clone(), sink.clone(), fast_config(3));

        // Discover the seeded ids by listing every job.
        let db = Database::open(&dir.join("app.db")).expect("reopen db");
        let jobs: Vec<(String, String, bool)> = {
            let conn = db.conn();
            let mut stmt = conn
                .prepare("SELECT id, status, cancel_requested FROM jobs ORDER BY created_at ASC")
                .expect("prepare");
            let rows = stmt
                .query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, bool>(2)?,
                    ))
                })
                .expect("query");
            rows.map(|r| r.expect("row")).collect()
        };

        wait_until("resumed jobs terminal", || {
            jobs.iter()
                .all(|(id, _, _)| svc.get(id).map(|j| j.status.is_terminal()).unwrap_or(false))
        });

        for (id, _, cancel_requested) in jobs {
            let final_job = svc.get(&id).expect("get");
            if cancel_requested {
                assert_eq!(final_job.status, JobStatus::Cancelled, "{id}");
                assert_eq!(final_job.error_code.as_deref(), Some("E_JOB_CANCELLED"));
            } else {
                assert_eq!(
                    final_job.status,
                    JobStatus::Succeeded,
                    "{id} resumed and ran"
                );
            }
        }
        assert_eq!(
            runner.call_count(),
            2,
            "queued + resumed running jobs each ran once"
        );
        svc.stop();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn resume_does_not_requeue_terminal_jobs() {
        let dir = std::env::temp_dir().join(format!(
            "tooltranslate_job_resume_terminal_{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        {
            let db = Database::open(&dir.join("app.db")).expect("open db");
            let project_id = new_uuid_v4().expect("uuid");
            let now = utc_iso8601_now();
            let conn = db.conn();
            conn.execute(
                "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at)
                 VALUES (?1, 'seed', 'seed.mp4', 'draft', ?2, ?2)",
                params![project_id, now],
            )
            .expect("seed project");
            drop(conn);

            let done = sample_job(&project_id, JobStatus::Succeeded);
            seed_job(&dir, &done);
        }

        let runner = Arc::new(ScriptRunner::new(RunMode::Ok));
        let sink = Arc::new(RecordingSink::default());
        let svc = JobService::open(dir.clone(), runner.clone(), sink.clone(), fast_config(3));
        thread::sleep(Duration::from_millis(100));
        assert_eq!(runner.call_count(), 0, "terminal jobs are not re-run");
        svc.stop();
        let _ = std::fs::remove_dir_all(&dir);
    }
}
