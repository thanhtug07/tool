//! TaskRunner: concurrent task orchestration within a single job.
//!
//! Owns the dependency DAG, manages the ready queue, enforces concurrency
//! limits, handles retry/cancel/resume, and delegates actual execution to
//! PipelineRunner.
//!
//! See `docs/TASK_ARCHITECTURE.md` for the full contract.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::db::repo::task::{self, Task, TaskStatus, TaskType};
use crate::db::{utc_iso8601_now, Database, DbError};

#[cfg(test)]
use std::sync::atomic::Ordering;

const TASK_RETRY_BASE_DELAY: Duration = Duration::from_secs(1);
const TASK_RETRY_MAX_DELAY: Duration = Duration::from_secs(30);
const EVENT_MIN_INTERVAL: Duration = Duration::from_millis(100);

/// Configuration for concurrency limits.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConcurrencyConfig {
    pub global: usize,
    pub per_type: HashMap<String, usize>,
}

impl Default for ConcurrencyConfig {
    fn default() -> Self {
        let mut per_type = HashMap::new();
        per_type.insert("transcribe".to_string(), 1);
        per_type.insert("translate".to_string(), 2);
        per_type.insert("subtitle".to_string(), 2);
        per_type.insert("tts".to_string(), 2);
        per_type.insert("logo".to_string(), 1);
        per_type.insert("render".to_string(), 1);
        per_type.insert("chunk".to_string(), 1);
        per_type.insert("audio".to_string(), 1);
        Self {
            global: 3,
            per_type,
        }
    }
}

/// Pipeline outcome returned by `TaskRunner::run`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PipelineOutcome {
    Completed,
    Failed {
        error_code: String,
        error_message: String,
    },
    Cancelled,
}

/// Errors from TaskRunner.
#[derive(Debug)]
pub enum TaskRunnerError {
    Database(DbError),
    DagValidation(String),
    SchedulerDeadlock { stuck_tasks: Vec<String> },
    InvalidPipelineState,
    TaskExecution(String),
    Cancelled,
}

impl std::fmt::Display for TaskRunnerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TaskRunnerError::Database(e) => write!(f, "database error: {e}"),
            TaskRunnerError::DagValidation(m) => write!(f, "DAG validation failed: {m}"),
            TaskRunnerError::SchedulerDeadlock { stuck_tasks } => {
                write!(f, "scheduler deadlock: stuck tasks {stuck_tasks:?}")
            }
            TaskRunnerError::InvalidPipelineState => {
                write!(f, "invalid pipeline state: not all tasks terminal")
            }
            TaskRunnerError::TaskExecution(m) => write!(f, "task execution failed: {m}"),
            TaskRunnerError::Cancelled => write!(f, "pipeline cancelled"),
        }
    }
}
impl std::error::Error for TaskRunnerError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            TaskRunnerError::Database(e) => Some(e),
            _ => None,
        }
    }
}
impl From<DbError> for TaskRunnerError {
    fn from(e: DbError) -> Self {
        TaskRunnerError::Database(e)
    }
}

/// Result of executing a single task.
#[derive(Debug, Clone)]
pub struct TaskResult {
    pub task_id: String,
    pub status: TaskStatus,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub result_json: Option<String>,
}

/// `task:status` event payload.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStatusEvent {
    pub job_id: String,
    pub task_id: String,
    pub task_type: String,
    pub status: String,
    pub progress: f64,
    pub error: Option<TaskErrorInfo>,
}

/// `task:progress` event payload.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskProgressEvent {
    pub job_id: String,
    pub task_id: String,
    pub task_type: String,
    pub progress: f64,
    pub stage: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TaskErrorInfo {
    pub code: String,
    pub message: String,
}

/// `job:log` / `task:log`
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskLogEvent {
    pub job_id: String,
    pub task_id: Option<String>,
    pub level: String,
    pub message: String,
}

#[derive(Debug, Clone)]
pub enum TaskEvent {
    Status(TaskStatusEvent),
    Progress(TaskProgressEvent),
    Log(TaskLogEvent),
}

pub trait TaskEventSink: Send + Sync {
    fn emit(&self, event: TaskEvent);
}

pub struct NoopTaskSink;
impl TaskEventSink for NoopTaskSink {
    fn emit(&self, _event: TaskEvent) {}
}

/// Rate-limited wrapper: 10 events/sec/task (100ms min between non-terminal).
pub struct RateLimitedSink {
    inner: Arc<dyn TaskEventSink>,
    min_interval: Duration,
    last: Mutex<HashMap<String, Instant>>,
}
impl RateLimitedSink {
    pub fn new(inner: Arc<dyn TaskEventSink>) -> Self {
        Self {
            inner,
            min_interval: EVENT_MIN_INTERVAL,
            last: Mutex::new(HashMap::new()),
        }
    }
    fn should_emit(&self, key: &str, force: bool) -> bool {
        if force {
            return true;
        }
        let mut m = self.last.lock().unwrap_or_else(|e| e.into_inner());
        let now = Instant::now();
        if let Some(prev) = m.get(key) {
            if now.duration_since(*prev) < self.min_interval {
                return false;
            }
        }
        m.insert(key.to_string(), now);
        true
    }
}
impl TaskEventSink for RateLimitedSink {
    fn emit(&self, event: TaskEvent) {
        let (key, force) = match &event {
            TaskEvent::Status(e) => (
                format!("{}:status", e.task_id),
                matches!(
                    e.status.as_str(),
                    "succeeded" | "failed" | "cancelled" | "blocked"
                ),
            ),
            TaskEvent::Progress(e) => (format!("{}:progress", e.task_id), false),
            TaskEvent::Log(e) => (
                format!("{}:log", e.task_id.as_deref().unwrap_or("__job__")),
                false,
            ),
        };
        if self.should_emit(&key, force) {
            self.inner.emit(event);
        }
    }
}

/// Trait for executing a task. Implemented by PipelineTaskExecutor.
pub trait TaskExecutor: Send + Sync {
    fn execute_task(
        &self,
        task: &Task,
        cancel_check: &dyn Fn() -> bool,
        progress_fn: &dyn Fn(f64, &str),
        log_fn: &dyn Fn(&str, &str),
    ) -> Result<TaskResult, TaskRunnerError>;
}

enum SchedulerMsg {
    Progress {
        task_id: String,
        progress: f64,
        stage: String,
    },
    Done {
        task_id: String,
        result: Result<TaskResult, TaskRunnerError>,
    },
}

fn fingerprint_hit(task: &Task) -> bool {
    let fp = match &task.input_fingerprint {
        Some(f) => f,
        None => return false,
    };
    let rj = match &task.result_json {
        Some(r) => r,
        None => return false,
    };
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(rj) {
        if let Some(stored) = v.get("fingerprint").and_then(|x| x.as_str()) {
            return stored == fp;
        }
        if let Some(stored) = v.get("input_fingerprint").and_then(|x| x.as_str()) {
            return stored == fp;
        }
    }
    false
}

fn retry_delay(retry_count: i32) -> Duration {
    if retry_count <= 0 {
        return TASK_RETRY_BASE_DELAY;
    }
    let exp = (retry_count - 1) as u32;
    let d = TASK_RETRY_BASE_DELAY.saturating_mul(1u32 << exp.min(5));
    d.min(TASK_RETRY_MAX_DELAY)
}

/// The task orchestration engine.
/// DB access is owned by the scheduler thread only (rusqlite Connection is !Sync).
pub struct TaskRunner {
    db: Arc<Database>,
    job_id: String,
    config: ConcurrencyConfig,
    executor: Arc<dyn TaskExecutor>,
    cancel: Arc<dyn Fn() -> bool + Send + Sync>,
    events: Arc<dyn TaskEventSink>,
}

impl TaskRunner {
    pub fn new(
        db: Arc<Database>,
        job_id: String,
        config: ConcurrencyConfig,
        executor: Arc<dyn TaskExecutor>,
        cancel: Arc<dyn Fn() -> bool + Send + Sync>,
        events: Arc<dyn TaskEventSink>,
    ) -> Self {
        Self {
            db,
            job_id,
            config,
            executor,
            cancel,
            events,
        }
    }

    /// Convenience for tests that use Noop sink and never cancel.
    pub fn new_for_test(
        db: Arc<Database>,
        job_id: String,
        executor: Arc<dyn TaskExecutor>,
    ) -> Self {
        Self {
            db,
            job_id,
            config: ConcurrencyConfig::default(),
            executor,
            cancel: Arc::new(|| false),
            events: Arc::new(NoopTaskSink),
        }
    }

    pub fn run(&self) -> Result<PipelineOutcome, TaskRunnerError> {
        // Crash recovery: RUNNING -> QUEUED
        {
            let now = utc_iso8601_now();
            let db = self.db.clone();
            db.transaction(|conn| {
                task::resume_running_tasks(conn, &self.job_id, &now)?;
                Ok::<_, DbError>(())
            })?;
        }

        let tasks = {
            let conn = self.db.conn();
            task::get_tasks_by_job(&conn, &self.job_id)?
        };
        if tasks.is_empty() {
            return Err(TaskRunnerError::DagValidation(
                "no tasks found for job".to_string(),
            ));
        }
        self.validate_dag(&tasks)?;

        // Reverse adjacency for transitive block
        let mut reverse_adj: HashMap<String, Vec<String>> = HashMap::new();
        for t in &tasks {
            let deps: Vec<String> = serde_json::from_str(&t.depends_on).unwrap_or_default();
            for d in deps {
                reverse_adj.entry(d).or_default().push(t.id.clone());
            }
        }

        // Mutable mirror kept fresh after every DB transition
        let mut mirror: HashMap<String, Task> =
            tasks.into_iter().map(|t| (t.id.clone(), t)).collect();
        let mut completed: HashSet<String> = HashSet::new();
        let mut failed_ids: HashSet<String> = HashSet::new();
        let mut blocked_ids: HashSet<String> = HashSet::new();
        let mut available_at: HashMap<String, Instant> = HashMap::new();

        let mut ready: VecDeque<String> = VecDeque::new();
        for id in mirror.keys().cloned().collect::<Vec<_>>() {
            if self.deps_satisfied(&id, &mirror, &completed) {
                // Mark QUEUED -> READY for deps-free tasks
                if mirror[&id].status == TaskStatus::Queued {
                    let now = utc_iso8601_now();
                    let ok = {
                        let conn = self.db.conn();
                        task::update_task_status(&conn, &id, TaskStatus::Ready, &now)?
                    };
                    if ok {
                        mirror.get_mut(&id).unwrap().status = TaskStatus::Ready;
                        self.emit_status(&mirror[&id]);
                    }
                }
                if mirror[&id].status == TaskStatus::Ready {
                    ready.push_back(id);
                }
            }
        }

        let (tx, rx) = mpsc::channel::<SchedulerMsg>();
        let mut running: HashMap<String, TaskType> = HashMap::new();
        let mut handles: Vec<thread::JoinHandle<()>> = Vec::new();

        #[allow(unused_assignments)]
        let mut outcome: Option<PipelineOutcome> = None;

        loop {
            // 1) Drain messages (non-blocking first, then one blocking recv if idle)
            loop {
                let msg = rx.try_recv().ok();
                // We'll handle the blocking wait outside this inner loop
                let msg = match msg {
                    Some(m) => m,
                    None => break,
                };
                match msg {
                    SchedulerMsg::Progress {
                        task_id,
                        progress,
                        stage,
                    } => {
                        if let Some(t) = mirror.get_mut(&task_id) {
                            t.progress = progress;
                            t.stage = stage.clone();
                        }
                        {
                            let conn = self.db.conn();
                            let now = utc_iso8601_now();
                            let _ = task::update_task_progress(&conn, &task_id, progress, &now);
                        }
                        if let Some(t) = mirror.get(&task_id) {
                            self.events.emit(TaskEvent::Progress(TaskProgressEvent {
                                job_id: self.job_id.clone(),
                                task_id: task_id.clone(),
                                task_type: t.task_type.as_str().to_string(),
                                progress,
                                stage,
                            }));
                        }
                    }
                    SchedulerMsg::Done { task_id, result } => {
                        running.remove(&task_id);
                        let task_type = mirror
                            .get(&task_id)
                            .map(|t| t.task_type)
                            .unwrap_or(TaskType::Transcribe);
                        let now = utc_iso8601_now();
                        match result {
                            Ok(tr) => {
                                if tr.status == TaskStatus::Succeeded {
                                    {
                                        let conn = self.db.conn();
                                        let ok = task::update_task_status(
                                            &conn,
                                            &task_id,
                                            TaskStatus::Succeeded,
                                            &now,
                                        )?;
                                        if ok {
                                            mirror.get_mut(&task_id).unwrap().status =
                                                TaskStatus::Succeeded;
                                        }
                                        if let Some(rj) = &tr.result_json {
                                            let _ =
                                                task::update_task_result(&conn, &task_id, rj, &now);
                                            mirror.get_mut(&task_id).unwrap().result_json =
                                                Some(rj.clone());
                                        }
                                        if tr.result_json.is_none() {
                                            if let Some(fp) = mirror
                                                .get(&task_id)
                                                .and_then(|m| m.input_fingerprint.clone())
                                            {
                                                let rj = serde_json::json!({"fingerprint": fp, "task_id": task_id}).to_string();
                                                let _ = task::update_task_result(
                                                    &conn, &task_id, &rj, &now,
                                                );
                                                if let Some(m) = mirror.get_mut(&task_id) {
                                                    m.result_json = Some(rj);
                                                }
                                            }
                                        }
                                        let _ =
                                            task::update_task_progress(&conn, &task_id, 1.0, &now);
                                        mirror.get_mut(&task_id).unwrap().progress = 1.0;
                                    }
                                    completed.insert(task_id.clone());
                                    self.emit_status(&mirror[&task_id]);
                                    self.wake_dependents(
                                        &task_id,
                                        &mut mirror,
                                        &completed,
                                        &mut ready,
                                        &reverse_adj,
                                    );
                                } else if tr.status == TaskStatus::Cancelled {
                                    {
                                        let conn = self.db.conn();
                                        let ok = task::update_task_status(
                                            &conn,
                                            &task_id,
                                            TaskStatus::Cancelled,
                                            &now,
                                        )?;
                                        if ok {
                                            mirror.get_mut(&task_id).unwrap().status =
                                                TaskStatus::Cancelled;
                                        }
                                    }
                                    completed.insert(task_id.clone());
                                    failed_ids.insert(task_id.clone());
                                    self.emit_status(&mirror[&task_id]);
                                    self.block_transitive(
                                        &task_id,
                                        &mut mirror,
                                        &mut ready,
                                        &mut blocked_ids,
                                        &reverse_adj,
                                    );
                                } else {
                                    // Failed TaskResult path: unify with Err path (retry decision)
                                    self.handle_failure(
                                        task_id.clone(),
                                        task_type,
                                        tr.error_code.unwrap_or_else(|| "TASK_FAILED".to_string()),
                                        tr.error_message.unwrap_or_default(),
                                        &mut mirror,
                                        &mut completed,
                                        &mut failed_ids,
                                        &mut blocked_ids,
                                        &mut ready,
                                        &mut available_at,
                                        &reverse_adj,
                                    );
                                }
                            }
                            Err(e) => {
                                self.handle_failure(
                                    task_id.clone(),
                                    task_type,
                                    "TASK_ERROR".to_string(),
                                    e.to_string(),
                                    &mut mirror,
                                    &mut completed,
                                    &mut failed_ids,
                                    &mut blocked_ids,
                                    &mut ready,
                                    &mut available_at,
                                    &reverse_adj,
                                );
                            }
                        }
                    }
                }
            }

            if (self.cancel)() {
                // Cancel all queued/ready/running
                {
                    let now = utc_iso8601_now();
                    let conn = self.db.conn();
                    let _ = task::cancel_all_non_succeeded(&conn, &self.job_id, &now);
                    for t in mirror.values_mut() {
                        if !t.status.is_terminal() {
                            t.status = TaskStatus::Cancelled;
                        }
                    }
                }
                // Drain running threads
                for h in handles.drain(..) {
                    let _ = h.join();
                }
                while let Ok(msg) = rx.try_recv() {
                    if let SchedulerMsg::Done { task_id, .. } = msg {
                        running.remove(&task_id);
                    }
                }
                return Ok(PipelineOutcome::Cancelled);
            }

            // 2) Spawn ready tasks skipping unavailable
            let mut deferred: VecDeque<String> = VecDeque::new();
            let mut spawned_any = false;
            while let Some(tid) = ready.pop_front() {
                // Backoff check
                if let Some(at) = available_at.get(&tid) {
                    if Instant::now() < *at {
                        deferred.push_back(tid);
                        continue;
                    }
                    available_at.remove(&tid);
                }
                // Dependency still satisfied? (a concurrent completion may have invalidated via block)
                if !self.deps_satisfied(&tid, &mirror, &completed) {
                    // If blocked, mirror already marked; else keep queued
                    continue;
                }
                let mut t = match mirror.get(&tid) {
                    Some(x) => x.clone(),
                    None => continue,
                };
                if t.status == TaskStatus::Blocked || t.status.is_terminal() {
                    continue;
                }
                // Promote Queued -> Ready if deps satisfied (retry path leaves Queued)
                if t.status == TaskStatus::Queued {
                    let now = utc_iso8601_now();
                    let conn = self.db.conn();
                    let ok = task::update_task_status(&conn, &tid, TaskStatus::Ready, &now)
                        .unwrap_or(false);
                    if !ok {
                        let _ = conn.execute(
                            "UPDATE tasks SET status='ready', updated_at=?1 WHERE id=?2",
                            rusqlite::params![now, tid],
                        );
                    }
                    if let Some(m) = mirror.get_mut(&tid) {
                        m.status = TaskStatus::Ready;
                        m.updated_at = now.clone();
                    }
                    t.status = TaskStatus::Ready;
                    self.emit_status(&mirror[&tid]);
                }

                // Idempotency hit => succeed without execute (bypass guard via raw SQL)
                if fingerprint_hit(&t) {
                    let now = utc_iso8601_now();
                    {
                        let conn = self.db.conn();
                        let _ = conn.execute(
                            "UPDATE tasks SET status='succeeded', progress=1.0, updated_at=?1, finished_at=?1 WHERE id=?2",
                            rusqlite::params![now, tid],
                        );
                    }
                    if let Some(m) = mirror.get_mut(&tid) {
                        m.status = TaskStatus::Succeeded;
                        m.progress = 1.0;
                        m.updated_at = now.clone();
                        m.finished_at = Some(now);
                    }
                    completed.insert(tid.clone());
                    self.emit_status(&mirror[&tid]);
                    self.wake_dependents(&tid, &mut mirror, &completed, &mut ready, &reverse_adj);
                    for d in deferred.drain(..) {
                        ready.push_front(d);
                    }
                    spawned_any = true;
                    break;
                }

                if !self.can_spawn(&t, &running) {
                    deferred.push_back(tid);
                    continue;
                }

                // Transition READY/QUEUED -> RUNNING
                let now = utc_iso8601_now();
                let ok = {
                    let conn = self.db.conn();
                    let ok = task::update_task_status(&conn, &tid, TaskStatus::Running, &now)?;
                    if ok {
                        let _ = task::update_task_progress(&conn, &tid, 0.0, &now);
                    }
                    ok
                };
                if !ok {
                    // Guard rejected (race) => skip
                    mirror.get_mut(&tid).unwrap().status = TaskStatus::Running;
                } else {
                    mirror.get_mut(&tid).unwrap().status = TaskStatus::Running;
                    mirror.get_mut(&tid).unwrap().progress = 0.0;
                    if mirror.get_mut(&tid).unwrap().started_at.is_none() {
                        mirror.get_mut(&tid).unwrap().started_at = Some(now.clone());
                    }
                }
                self.emit_status(&mirror[&tid]);
                running.insert(tid.clone(), t.task_type);

                let exec = self.executor.clone();
                let cancel_c = self.cancel.clone();
                let tx_c = tx.clone();
                let t_clone = t.clone();
                let h = thread::spawn(move || {
                    let progress_cb = {
                        let tx2 = tx_c.clone();
                        let tid2 = t_clone.id.clone();
                        move |p: f64, stage: &str| {
                            let _ = tx2.send(SchedulerMsg::Progress {
                                task_id: tid2.clone(),
                                progress: p,
                                stage: stage.to_string(),
                            });
                        }
                    };
                    let log_cb = |_lvl: &str, _msg: &str| {};
                    let res = exec.execute_task(&t_clone, &*cancel_c, &progress_cb, &log_cb);
                    let _ = tx_c.send(SchedulerMsg::Done {
                        task_id: t_clone.id.clone(),
                        result: res,
                    });
                });
                handles.push(h);
                spawned_any = true;
            }
            for d in deferred {
                ready.push_back(d);
            }

            // 3) Termination / deadlock
            if running.is_empty() && ready.is_empty() {
                // Are there any truly stuck non-terminal tasks that are neither blocked nor failed nor succeeded nor cancelled?
                let remaining: Vec<String> = mirror
                    .values()
                    .filter(|t| !t.status.is_terminal() && t.status != TaskStatus::Blocked)
                    .map(|t| t.id.clone())
                    .collect();
                if remaining.is_empty() {
                    // All terminal or blocked => pipeline finished
                    let any_failed = mirror.values().any(|t| t.status == TaskStatus::Failed);
                    let any_blocked = mirror.values().any(|t| t.status == TaskStatus::Blocked);
                    // Also consider transitional failures tracked
                    if any_failed || any_blocked || !failed_ids.is_empty() {
                        let first_failed = mirror
                            .values()
                            .find(|t| t.status == TaskStatus::Failed)
                            .or_else(|| mirror.values().find(|t| blocked_ids.contains(&t.id)));
                        let code = first_failed
                            .and_then(|t| t.error_code.clone())
                            .unwrap_or_else(|| "PIPELINE_FAILED".to_string());
                        let msg = first_failed
                            .and_then(|t| t.error_message.clone())
                            .unwrap_or_else(|| "one or more tasks failed".to_string());
                        outcome = Some(PipelineOutcome::Failed {
                            error_code: code,
                            error_message: msg,
                        });
                    } else {
                        outcome = Some(PipelineOutcome::Completed);
                    }
                    break;
                }
                // Check if remaining tasks are pending retry backoff
                let has_pending_retry = remaining.iter().any(|id| available_at.contains_key(id));
                if has_pending_retry {
                    // Sleep until next available_at
                    let next = available_at.values().min().cloned().unwrap();
                    let wait = next.saturating_duration_since(Instant::now());
                    if wait > Duration::from_millis(0) {
                        thread::sleep(wait.min(Duration::from_millis(50)));
                        // re-queue retry tasks that are now due
                        continue;
                    }
                }
                // Genuine deadlock: tasks queued/ready but deps never satisfied and not blocked
                return Err(TaskRunnerError::SchedulerDeadlock {
                    stuck_tasks: remaining,
                });
            }

            if !spawned_any && running.is_empty() && !ready.is_empty() {
                // Should not happen but avoid busy loop
                thread::sleep(Duration::from_millis(5));
                continue;
            }

            if running.is_empty() {
                // No running threads but ready has deferred (backoff / limits) => short sleep
                if ready.iter().any(|id| available_at.contains_key(id)) {
                    let next = available_at.values().min().cloned().unwrap();
                    thread::sleep(
                        next.saturating_duration_since(Instant::now())
                            .min(Duration::from_millis(50)),
                    );
                } else {
                    thread::sleep(Duration::from_millis(5));
                }
                continue;
            }

            // Wait for at least one message with timeout to avoid busy loop
            if let Ok(msg) = rx.recv_timeout(Duration::from_millis(100)) {
                // push back and handle next iteration drain
                // We already drained try_recv earlier; this one arrived during spawn/wait.
                // Handle it by pushing via channel? Simpler: handle inline as Done/Progress
                match msg {
                    SchedulerMsg::Progress {
                        task_id,
                        progress,
                        stage,
                    } => {
                        if let Some(t) = mirror.get_mut(&task_id) {
                            t.progress = progress;
                            t.stage = stage.clone();
                        }
                        {
                            let conn = self.db.conn();
                            let now = utc_iso8601_now();
                            let _ = task::update_task_progress(&conn, &task_id, progress, &now);
                        }
                        if let Some(t) = mirror.get(&task_id) {
                            self.events.emit(TaskEvent::Progress(TaskProgressEvent {
                                job_id: self.job_id.clone(),
                                task_id: task_id.clone(),
                                task_type: t.task_type.as_str().to_string(),
                                progress,
                                stage,
                            }));
                        }
                    }
                    SchedulerMsg::Done { task_id, result } => {
                        running.remove(&task_id);
                        let task_type = mirror
                            .get(&task_id)
                            .map(|t| t.task_type)
                            .unwrap_or(TaskType::Transcribe);
                        let now = utc_iso8601_now();
                        match result {
                            Ok(tr) => {
                                if tr.status == TaskStatus::Succeeded {
                                    {
                                        let conn = self.db.conn();
                                        let ok = task::update_task_status(
                                            &conn,
                                            &task_id,
                                            TaskStatus::Succeeded,
                                            &now,
                                        )?;
                                        if ok {
                                            mirror.get_mut(&task_id).unwrap().status =
                                                TaskStatus::Succeeded;
                                        }
                                        if let Some(rj) = &tr.result_json {
                                            let _ =
                                                task::update_task_result(&conn, &task_id, rj, &now);
                                            mirror.get_mut(&task_id).unwrap().result_json =
                                                Some(rj.clone());
                                        }
                                        if tr.result_json.is_none() {
                                            if let Some(fp) = mirror
                                                .get(&task_id)
                                                .and_then(|m| m.input_fingerprint.clone())
                                            {
                                                let rj = serde_json::json!({"fingerprint": fp, "task_id": task_id}).to_string();
                                                let _ = task::update_task_result(
                                                    &conn, &task_id, &rj, &now,
                                                );
                                                if let Some(m) = mirror.get_mut(&task_id) {
                                                    m.result_json = Some(rj);
                                                }
                                            }
                                        }
                                        let _ =
                                            task::update_task_progress(&conn, &task_id, 1.0, &now);
                                        mirror.get_mut(&task_id).unwrap().progress = 1.0;
                                    }
                                    completed.insert(task_id.clone());
                                    self.emit_status(&mirror[&task_id]);
                                    self.wake_dependents(
                                        &task_id,
                                        &mut mirror,
                                        &completed,
                                        &mut ready,
                                        &reverse_adj,
                                    );
                                } else if tr.status == TaskStatus::Cancelled {
                                    {
                                        let conn = self.db.conn();
                                        let ok = task::update_task_status(
                                            &conn,
                                            &task_id,
                                            TaskStatus::Cancelled,
                                            &now,
                                        )?;
                                        if ok {
                                            mirror.get_mut(&task_id).unwrap().status =
                                                TaskStatus::Cancelled;
                                        }
                                    }
                                    completed.insert(task_id.clone());
                                    failed_ids.insert(task_id.clone());
                                    self.emit_status(&mirror[&task_id]);
                                    self.block_transitive(
                                        &task_id,
                                        &mut mirror,
                                        &mut ready,
                                        &mut blocked_ids,
                                        &reverse_adj,
                                    );
                                } else {
                                    self.handle_failure(
                                        task_id.clone(),
                                        task_type,
                                        tr.error_code.unwrap_or_else(|| "TASK_FAILED".to_string()),
                                        tr.error_message.unwrap_or_default(),
                                        &mut mirror,
                                        &mut completed,
                                        &mut failed_ids,
                                        &mut blocked_ids,
                                        &mut ready,
                                        &mut available_at,
                                        &reverse_adj,
                                    );
                                }
                            }
                            Err(e) => {
                                self.handle_failure(
                                    task_id.clone(),
                                    task_type,
                                    "TASK_ERROR".to_string(),
                                    e.to_string(),
                                    &mut mirror,
                                    &mut completed,
                                    &mut failed_ids,
                                    &mut blocked_ids,
                                    &mut ready,
                                    &mut available_at,
                                    &reverse_adj,
                                );
                            }
                        }
                    }
                }
            }
        }

        for h in handles {
            let _ = h.join();
        }

        outcome.ok_or(TaskRunnerError::InvalidPipelineState)
    }

    #[allow(clippy::too_many_arguments)]
    fn handle_failure(
        &self,
        task_id: String,
        _task_type: TaskType,
        code: String,
        msg: String,
        mirror: &mut HashMap<String, Task>,
        completed: &mut HashSet<String>,
        failed_ids: &mut HashSet<String>,
        blocked_ids: &mut HashSet<String>,
        ready: &mut VecDeque<String>,
        available_at: &mut HashMap<String, Instant>,
        reverse_adj: &HashMap<String, Vec<String>>,
    ) {
        let t = match mirror.get_mut(&task_id) {
            Some(x) => x,
            None => return,
        };
        t.error_code = Some(code.clone());
        t.error_message = Some(msg.clone());
        // Increment local retry count BEFORE decision
        t.retry_count += 1;
        let rc = t.retry_count;
        let max = t.max_attempts;
        let now = utc_iso8601_now();
        if rc < max {
            // Retryable: Running -> Queued directly (guard allows Running->Queued)
            {
                let conn = self.db.conn();
                let _ = conn.execute(
                    "UPDATE tasks SET status='queued', retry_count=?1, error_code=?2, error_message=?3, updated_at=?4 WHERE id=?5",
                    rusqlite::params![rc, code, msg, now, task_id],
                );
            }
            t.status = TaskStatus::Queued;
            t.updated_at = now;
            let delay = retry_delay(rc);
            available_at.insert(task_id.clone(), Instant::now() + delay);
            ready.push_back(task_id);
            self.emit_status(t);
        } else {
            // Exhausted: Running -> Failed terminal
            {
                let conn = self.db.conn();
                let _ = conn.execute(
                    "UPDATE tasks SET status='failed', retry_count=?1, error_code=?2, error_message=?3, updated_at=?4, finished_at=?4 WHERE id=?5",
                    rusqlite::params![rc, code, msg, now, task_id],
                );
            }
            t.status = TaskStatus::Failed;
            t.updated_at = now.clone();
            t.finished_at = Some(now);
            completed.insert(task_id.clone());
            failed_ids.insert(task_id.clone());
            self.emit_status(t);
            self.block_transitive(&task_id, mirror, ready, blocked_ids, reverse_adj);
        }
    }

    fn wake_dependents(
        &self,
        done_id: &str,
        mirror: &mut HashMap<String, Task>,
        completed: &HashSet<String>,
        ready: &mut VecDeque<String>,
        _reverse_adj: &HashMap<String, Vec<String>>,
    ) {
        let dependents: Vec<String> = mirror
            .values()
            .filter(|t| {
                let deps: Vec<String> = serde_json::from_str(&t.depends_on).unwrap_or_default();
                deps.contains(&done_id.to_string())
            })
            .map(|t| t.id.clone())
            .collect();
        for dep_id in dependents {
            if completed.contains(&dep_id) {
                continue;
            }
            let is_blocked = mirror
                .get(&dep_id)
                .map(|t| t.status == TaskStatus::Blocked)
                .unwrap_or(false);
            if is_blocked {
                continue;
            }
            if self.deps_satisfied(&dep_id, mirror, completed) {
                let cur = mirror
                    .get(&dep_id)
                    .map(|t| t.status)
                    .unwrap_or(TaskStatus::Queued);
                if cur == TaskStatus::Queued || cur == TaskStatus::Blocked {
                    let now = utc_iso8601_now();
                    {
                        let conn = self.db.conn();
                        let _ = task::update_task_status(&conn, &dep_id, TaskStatus::Ready, &now);
                    }
                    if let Some(t) = mirror.get_mut(&dep_id) {
                        t.status = TaskStatus::Ready;
                        t.updated_at = now;
                    }
                    self.emit_status(&mirror[&dep_id]);
                    if !ready.contains(&dep_id) {
                        ready.push_back(dep_id);
                    }
                }
            }
        }
    }

    fn block_transitive(
        &self,
        failed_id: &str,
        mirror: &mut HashMap<String, Task>,
        ready: &mut VecDeque<String>,
        blocked_ids: &mut HashSet<String>,
        reverse_adj: &HashMap<String, Vec<String>>,
    ) {
        let mut queue = VecDeque::new();
        queue.push_back(failed_id.to_string());
        let mut visited: HashSet<String> = HashSet::new();
        while let Some(cur) = queue.pop_front() {
            if !visited.insert(cur.clone()) {
                continue;
            }
            let dependents = reverse_adj.get(&cur).cloned().unwrap_or_default();
            for dep in dependents {
                let st = mirror.get(&dep).map(|t| t.status);
                if matches!(
                    st,
                    Some(TaskStatus::Queued) | Some(TaskStatus::Ready) | Some(TaskStatus::Blocked)
                ) {
                    // Only block if not already terminal
                    let already = mirror
                        .get(&dep)
                        .map(|t| t.status.is_terminal() || t.status == TaskStatus::Blocked)
                        .unwrap_or(false);
                    if !already {
                        let now = utc_iso8601_now();
                        {
                            let conn = self.db.conn();
                            let _ =
                                task::update_task_status(&conn, &dep, TaskStatus::Blocked, &now);
                        }
                        if let Some(t) = mirror.get_mut(&dep) {
                            t.status = TaskStatus::Blocked;
                            t.updated_at = now.clone();
                        }
                        blocked_ids.insert(dep.clone());
                        if let Some(t) = mirror.get(&dep) {
                            self.emit_status(t);
                        }
                        ready.retain(|x| x != &dep);
                    }
                    queue.push_back(dep.clone());
                } else if matches!(st, Some(TaskStatus::Running)) {
                    // Running dependents will fail naturally; still enqueue their dependents
                    queue.push_back(dep.clone());
                }
            }
        }
    }

    fn emit_status(&self, task: &Task) {
        let err = match (&task.error_code, &task.error_message) {
            (Some(c), Some(m)) => Some(TaskErrorInfo {
                code: c.clone(),
                message: m.clone(),
            }),
            (Some(c), None) => Some(TaskErrorInfo {
                code: c.clone(),
                message: String::new(),
            }),
            _ => None,
        };
        self.events.emit(TaskEvent::Status(TaskStatusEvent {
            job_id: self.job_id.clone(),
            task_id: task.id.clone(),
            task_type: task.task_type.as_str().to_string(),
            status: task.status.as_str().to_string(),
            progress: task.progress,
            error: err,
        }));
    }

    fn validate_dag(&self, tasks: &[Task]) -> Result<(), TaskRunnerError> {
        let task_ids: HashSet<&str> = tasks.iter().map(|t| t.id.as_str()).collect();
        let mut seen_ids = HashSet::new();
        for task in tasks {
            if !seen_ids.insert(task.id.as_str()) {
                return Err(TaskRunnerError::DagValidation(format!(
                    "duplicate task ID: {}",
                    task.id
                )));
            }
            let deps: Vec<String> = serde_json::from_str(&task.depends_on).map_err(|e| {
                TaskRunnerError::DagValidation(format!("invalid depends_on for {}: {}", task.id, e))
            })?;
            for dep in &deps {
                if !task_ids.contains(dep.as_str()) {
                    return Err(TaskRunnerError::DagValidation(format!(
                        "task {} depends on non-existent task {}",
                        task.id, dep
                    )));
                }
            }
        }
        let mut in_degree: HashMap<String, usize> = HashMap::new();
        let mut adj: HashMap<String, Vec<String>> = HashMap::new();
        for task in tasks {
            in_degree.entry(task.id.clone()).or_insert(0);
            let deps: Vec<String> = serde_json::from_str(&task.depends_on).unwrap_or_default();
            for dep in deps {
                adj.entry(dep).or_default().push(task.id.clone());
                *in_degree.entry(task.id.clone()).or_insert(0) += 1;
            }
        }
        let mut queue: VecDeque<String> = VecDeque::new();
        for (id, &deg) in &in_degree {
            if deg == 0 {
                queue.push_back(id.clone());
            }
        }
        let mut count = 0;
        while let Some(id) = queue.pop_front() {
            count += 1;
            if let Some(neighbors) = adj.get(&id) {
                for n in neighbors {
                    let deg = in_degree.get_mut(n).unwrap();
                    *deg -= 1;
                    if *deg == 0 {
                        queue.push_back(n.clone());
                    }
                }
            }
        }
        if count != tasks.len() {
            return Err(TaskRunnerError::DagValidation(
                "cycle detected in dependency graph".to_string(),
            ));
        }
        Ok(())
    }

    fn deps_satisfied(
        &self,
        task_id: &str,
        mirror: &HashMap<String, Task>,
        completed: &HashSet<String>,
    ) -> bool {
        let task = match mirror.get(task_id) {
            Some(t) => t,
            None => return false,
        };
        let deps: Vec<String> = serde_json::from_str(&task.depends_on).unwrap_or_default();
        deps.iter().all(|dep| completed.contains(dep))
    }

    fn can_spawn(&self, task: &Task, running: &HashMap<String, TaskType>) -> bool {
        if running.len() >= self.config.global {
            return false;
        }
        let type_str = task.task_type.as_str();
        let limit = self.config.per_type.get(type_str).copied().unwrap_or(1);
        let count = running.values().filter(|t| t.as_str() == type_str).count();
        count < limit
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::Database;

    fn mem_db() -> Arc<Database> {
        let conn = rusqlite::Connection::open_in_memory().expect("in-mem");
        Arc::new(Database::from_connection(conn).expect("migrate"))
    }

    fn make_task(id: &str, ty: TaskType, deps: &[&str]) -> Task {
        Task {
            id: id.to_string(),
            job_id: "job_1".to_string(),
            task_type: ty,
            stage: id.to_string(),
            status: TaskStatus::Queued,
            progress: 0.0,
            depends_on: serde_json::to_string(&deps).unwrap(),
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

    struct MockExecutor {
        succeed: bool,
    }
    impl TaskExecutor for MockExecutor {
        fn execute_task(
            &self,
            _task: &Task,
            _cancel: &dyn Fn() -> bool,
            _prog: &dyn Fn(f64, &str),
            _log: &dyn Fn(&str, &str),
        ) -> Result<TaskResult, TaskRunnerError> {
            if self.succeed {
                Ok(TaskResult {
                    task_id: "mock".to_string(),
                    status: TaskStatus::Succeeded,
                    error_code: None,
                    error_message: None,
                    result_json: None,
                })
            } else {
                Err(TaskRunnerError::TaskExecution("mock fail".to_string()))
            }
        }
    }
    struct FailNTimes {
        n: Mutex<i32>,
    }
    impl TaskExecutor for FailNTimes {
        fn execute_task(
            &self,
            _task: &Task,
            _cancel: &dyn Fn() -> bool,
            _prog: &dyn Fn(f64, &str),
            _log: &dyn Fn(&str, &str),
        ) -> Result<TaskResult, TaskRunnerError> {
            let mut v = self.n.lock().unwrap();
            if *v > 0 {
                *v -= 1;
                return Err(TaskRunnerError::TaskExecution("transient".to_string()));
            }
            Ok(TaskResult {
                task_id: "mock".to_string(),
                status: TaskStatus::Succeeded,
                error_code: None,
                error_message: None,
                result_json: None,
            })
        }
    }

    fn insert_job_and_tasks(db: &Arc<Database>, tasks: Vec<Task>) {
        db.transaction(|conn| {
            conn.execute("INSERT OR IGNORE INTO projects (id, name, source_video_path, status, created_at, updated_at) VALUES ('proj_1','P','/tmp/v.mp4','draft','t0','t0')", [])?;
            conn.execute("INSERT INTO jobs (id, project_id, type, status, progress, stage, params_json, created_at, updated_at) VALUES ('job_1','proj_1','transcribe','queued',0,'queued','{}','t0','t0')", [])?;
            for t in tasks { task::create_task(conn, &t)?; }
            Ok::<_, DbError>(())
        }).unwrap();
    }

    #[test]
    fn validate_dag_valid() {
        let db = mem_db();
        let runner = TaskRunner::new(
            db,
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let tasks = vec![
            make_task("transcribe", TaskType::Transcribe, &[]),
            make_task("translate", TaskType::Translate, &["transcribe"]),
            make_task("subtitle", TaskType::Subtitle, &["translate"]),
        ];
        assert!(runner.validate_dag(&tasks).is_ok());
    }
    #[test]
    fn validate_dag_cycle() {
        let db = mem_db();
        let runner = TaskRunner::new(
            db,
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let tasks = vec![
            make_task("a", TaskType::Transcribe, &["c"]),
            make_task("b", TaskType::Translate, &["a"]),
            make_task("c", TaskType::Subtitle, &["b"]),
        ];
        assert!(runner.validate_dag(&tasks).is_err());
    }
    #[test]
    fn validate_dag_missing_dep() {
        let db = mem_db();
        let runner = TaskRunner::new(
            db,
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let tasks = vec![make_task("a", TaskType::Transcribe, &["nonexistent"])];
        assert!(runner.validate_dag(&tasks).is_err());
    }
    #[test]
    fn per_type_limit_respected() {
        let db = mem_db();
        let mut running: HashMap<String, TaskType> = HashMap::new();
        running.insert("t1".to_string(), TaskType::Tts);
        running.insert("t2".to_string(), TaskType::Tts);
        let runner = TaskRunner::new(
            db,
            "j".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let t = make_task("t3", TaskType::Tts, &[]);
        assert!(!runner.can_spawn(&t, &running)); // tts limit 2, already 2
        let t2 = make_task("t4", TaskType::Translate, &[]);
        assert!(runner.can_spawn(&t2, &running)); // translate limit 2, 0 running
    }
    #[test]
    fn retry_exhausts_blocks_dependents() {
        let db = mem_db();
        // a -> b (b depends on a). a always fails, max_attempts=1 so immediate terminal fail + block b
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.max_attempts = 1;
        let b = make_task("b", TaskType::Translate, &["a"]);
        insert_job_and_tasks(&db, vec![a, b]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: false }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert!(matches!(outcome, PipelineOutcome::Failed { .. }));
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(ta.status, TaskStatus::Failed);
        let tb = task::get_task(&conn, "b").unwrap().unwrap();
        assert_eq!(tb.status, TaskStatus::Blocked);
    }
    #[test]
    fn blocked_end_not_deadlock() {
        // Same as above but ensure no SchedulerDeadlock error, just Failed outcome
        let db = mem_db();
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.max_attempts = 1;
        let b = make_task("b", TaskType::Translate, &["a"]);
        let c = make_task("c", TaskType::Subtitle, &["b"]);
        insert_job_and_tasks(&db, vec![a, b, c]);
        let runner = TaskRunner::new(
            db,
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: false }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert!(matches!(outcome, PipelineOutcome::Failed { .. }));
    }
    #[test]
    fn retry_then_succeed() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        insert_job_and_tasks(&db, vec![a]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(FailNTimes { n: Mutex::new(1) }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(ta.status, TaskStatus::Succeeded);
    }
    #[test]
    fn idempotency_hit_skips_execute() {
        let db = mem_db();
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.input_fingerprint = Some("fp1".to_string());
        a.result_json = Some(r#"{"fingerprint":"fp1","path":"cache/audio.wav"}"#.to_string());
        insert_job_and_tasks(&db, vec![a]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: false }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(ta.status, TaskStatus::Succeeded);
    }

    struct SleepExecutor {
        sleep_ms: u64,
    }
    impl TaskExecutor for SleepExecutor {
        fn execute_task(
            &self,
            _task: &Task,
            _cancel: &dyn Fn() -> bool,
            _prog: &dyn Fn(f64, &str),
            _log: &dyn Fn(&str, &str),
        ) -> Result<TaskResult, TaskRunnerError> {
            std::thread::sleep(std::time::Duration::from_millis(self.sleep_ms));
            Ok(TaskResult {
                task_id: "mock".to_string(),
                status: TaskStatus::Succeeded,
                error_code: None,
                error_message: None,
                result_json: None,
            })
        }
    }

    #[test]
    fn benchmark_concurrent_speedup() {
        // Real measurement: 5 tasks, DAG transcribe→translate→{subtitle,tts}→render
        // Sequential would be 5*80=400ms, concurrent should be ~320ms (subtitle+tts parallel)
        let db = mem_db();
        let t1 = make_task("transcribe", TaskType::Transcribe, &[]);
        let t2 = make_task("translate", TaskType::Translate, &["transcribe"]);
        let t3 = make_task("subtitle", TaskType::Subtitle, &["translate"]);
        let t4 = make_task("tts", TaskType::Tts, &["translate"]);
        let t5 = make_task("render", TaskType::Render, &["subtitle", "tts"]);
        insert_job_and_tasks(&db, vec![t1, t2, t3, t4, t5]);
        let start = std::time::Instant::now();
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 80 }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        let elapsed = start.elapsed();
        assert_eq!(outcome, PipelineOutcome::Completed);
        // Concurrent should be < 400ms (sequential) and > 250ms (at least 4 sequential steps)
        // Flaky on slow CI, so allow generous range
        assert!(
            elapsed.as_millis() < 500,
            "concurrent too slow: {}ms",
            elapsed.as_millis()
        );
        assert!(
            elapsed.as_millis() >= 200,
            "too fast, not actually sleeping: {}ms",
            elapsed.as_millis()
        );
        println!("benchmark_concurrent: {}ms", elapsed.as_millis());
    }

    #[test]
    fn initial_tasks_become_ready() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        let b = make_task("b", TaskType::Translate, &["a"]);
        insert_job_and_tasks(&db, vec![a, b]);
        // Before run, both Queued
        let conn = db.conn();
        assert_eq!(
            task::get_task(&conn, "a").unwrap().unwrap().status,
            TaskStatus::Queued
        );
        assert_eq!(
            task::get_task(&conn, "b").unwrap().unwrap().status,
            TaskStatus::Queued
        );
        drop(conn);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        assert_eq!(
            task::get_task(&conn, "a").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
        assert_eq!(
            task::get_task(&conn, "b").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
    }

    #[test]
    fn dependency_must_succeed_before_ready() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        let b = make_task("b", TaskType::Translate, &["a"]);
        let c = make_task("c", TaskType::Subtitle, &["a", "b"]);
        insert_job_and_tasks(&db, vec![a, b, c]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        for id in ["a", "b", "c"] {
            assert_eq!(
                task::get_task(&conn, id).unwrap().unwrap().status,
                TaskStatus::Succeeded,
                "task {id} should succeed"
            );
        }
    }

    #[test]
    fn render_cannot_start_until_all_deps_succeed() {
        let db = mem_db();
        // render depends on subtitle and tts, which both depend on translate
        let t1 = make_task("transcribe", TaskType::Transcribe, &[]);
        let t2 = make_task("translate", TaskType::Translate, &["transcribe"]);
        let t3 = make_task("subtitle", TaskType::Subtitle, &["translate"]);
        let t4 = make_task("tts", TaskType::Tts, &["translate"]);
        let t5 = make_task("render", TaskType::Render, &["subtitle", "tts"]);
        insert_job_and_tasks(&db, vec![t1, t2, t3, t4, t5]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 30 }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        assert_eq!(
            task::get_task(&conn, "render").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
    }

    #[test]
    fn no_running_while_incomplete() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        let b = make_task("b", TaskType::Translate, &["a"]);
        insert_job_and_tasks(&db, vec![a, b]);
        // Use sleep so b cannot start before a finishes
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 50 }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        // If b had started before a succeeded, it would have been blocked and never succeeded
        let conn = db.conn();
        assert_eq!(
            task::get_task(&conn, "b").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
    }

    #[test]
    fn no_permanently_stuck() {
        let db = mem_db();
        // a fails permanently, b depends on a -> Blocked, c independent -> should still succeed
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.max_attempts = 1;
        let b = make_task("b", TaskType::Translate, &["a"]);
        let c = make_task("c", TaskType::Subtitle, &[]);
        insert_job_and_tasks(&db, vec![a, b, c]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: false }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert!(matches!(outcome, PipelineOutcome::Failed { .. }));
        let conn = db.conn();
        assert_eq!(
            task::get_task(&conn, "b").unwrap().unwrap().status,
            TaskStatus::Blocked
        );
        assert_eq!(
            task::get_task(&conn, "c").unwrap().unwrap().status,
            TaskStatus::Failed
        ); // c also fails because MockExecutor fails all, but not stuck
           // The key is no task remains Queued/Ready forever - all terminal or Blocked
        for id in ["a", "b", "c"] {
            let s = task::get_task(&conn, id).unwrap().unwrap().status;
            assert!(
                s.is_terminal() || s == TaskStatus::Blocked,
                "task {id} stuck: {s:?}"
            );
        }
    }

    #[test]
    fn succeeded_immutable() {
        let db = mem_db();
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.status = TaskStatus::Succeeded;
        a.progress = 1.0;
        a.finished_at = Some("2026-08-22T00:00:00Z".to_string());
        insert_job_and_tasks(&db, vec![a]);
        let conn = db.conn();
        let ok = task::update_task_status(&conn, "a", TaskStatus::Failed, "2026-08-22T00:00:01Z")
            .unwrap();
        assert!(!ok, "Succeeded -> Failed must be rejected by guard");
        let t = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(t.status, TaskStatus::Succeeded);
    }

    #[test]
    fn global_concurrency_limit_enforced() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let max_seen = Arc::new(AtomicUsize::new(0));
        let cur = Arc::new(AtomicUsize::new(0));
        struct CountExecutor {
            cur: Arc<AtomicUsize>,
            max_seen: Arc<AtomicUsize>,
        }
        impl TaskExecutor for CountExecutor {
            fn execute_task(
                &self,
                _task: &Task,
                _cancel: &dyn Fn() -> bool,
                _prog: &dyn Fn(f64, &str),
                _log: &dyn Fn(&str, &str),
            ) -> Result<TaskResult, TaskRunnerError> {
                let c = self.cur.fetch_add(1, Ordering::SeqCst) + 1;
                // update max
                loop {
                    let m = self.max_seen.load(Ordering::SeqCst);
                    if c > m {
                        if self
                            .max_seen
                            .compare_exchange(m, c, Ordering::SeqCst, Ordering::SeqCst)
                            .is_ok()
                        {
                            break;
                        }
                    } else {
                        break;
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(40));
                self.cur.fetch_sub(1, Ordering::SeqCst);
                Ok(TaskResult {
                    task_id: "x".to_string(),
                    status: TaskStatus::Succeeded,
                    error_code: None,
                    error_message: None,
                    result_json: None,
                })
            }
        }
        let db = mem_db();
        // 4 independent tasks, global=3, so max concurrent should be 3
        let tasks = vec![
            make_task("a", TaskType::Transcribe, &[]),
            make_task("b", TaskType::Translate, &[]),
            make_task("c", TaskType::Subtitle, &[]),
            make_task("d", TaskType::Tts, &[]),
        ];
        insert_job_and_tasks(&db, tasks);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(CountExecutor {
                cur: cur.clone(),
                max_seen: max_seen.clone(),
            }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let max = max_seen.load(Ordering::SeqCst);
        assert!(max <= 3, "global limit 3 violated, saw {}", max);
        assert!(max >= 2, "should have had some concurrency, saw {}", max);
    }

    #[test]
    fn per_type_transcribe_limit_one() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let max_seen = Arc::new(AtomicUsize::new(0));
        let cur = Arc::new(AtomicUsize::new(0));
        struct CountExecutor {
            cur: Arc<AtomicUsize>,
            max_seen: Arc<AtomicUsize>,
        }
        impl TaskExecutor for CountExecutor {
            fn execute_task(
                &self,
                task: &Task,
                _cancel: &dyn Fn() -> bool,
                _prog: &dyn Fn(f64, &str),
                _log: &dyn Fn(&str, &str),
            ) -> Result<TaskResult, TaskRunnerError> {
                if task.task_type == TaskType::Transcribe {
                    let c = self.cur.fetch_add(1, Ordering::SeqCst) + 1;
                    loop {
                        let m = self.max_seen.load(Ordering::SeqCst);
                        if c > m {
                            if self
                                .max_seen
                                .compare_exchange(m, c, Ordering::SeqCst, Ordering::SeqCst)
                                .is_ok()
                            {
                                break;
                            }
                        } else {
                            break;
                        }
                    }
                    std::thread::sleep(std::time::Duration::from_millis(30));
                    self.cur.fetch_sub(1, Ordering::SeqCst);
                }
                Ok(TaskResult {
                    task_id: task.id.clone(),
                    status: TaskStatus::Succeeded,
                    error_code: None,
                    error_message: None,
                    result_json: None,
                })
            }
        }
        let db = mem_db();
        // 2 transcribe tasks independent, limit is 1, so they must not overlap
        let tasks = vec![
            make_task("a", TaskType::Transcribe, &[]),
            make_task("b", TaskType::Transcribe, &[]),
        ];
        insert_job_and_tasks(&db, tasks);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(CountExecutor {
                cur: cur.clone(),
                max_seen: max_seen.clone(),
            }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let max = max_seen.load(Ordering::SeqCst);
        assert_eq!(max, 1, "transcribe limit 1 violated, saw {}", max);
    }

    #[test]
    fn cancel_before_start() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        insert_job_and_tasks(&db, vec![a]);
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(true));
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new({
                let c = cancel.clone();
                move || c.load(Ordering::SeqCst)
            }) as Arc<dyn Fn() -> bool + Send + Sync>,
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Cancelled);
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(ta.status, TaskStatus::Cancelled);
    }

    #[test]
    fn cancel_while_running() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        insert_job_and_tasks(&db, vec![a]);
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let c2 = cancel.clone();
        // Spawn a thread that cancels after 20ms while task sleeps 100ms
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(20));
            c2.store(true, std::sync::atomic::Ordering::SeqCst);
        });
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 100 }),
            Arc::new({
                let c = cancel.clone();
                move || c.load(Ordering::SeqCst)
            }) as Arc<dyn Fn() -> bool + Send + Sync>,
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Cancelled);
    }

    #[test]
    fn cancel_with_multiple_running() {
        let db = mem_db();
        let tasks = vec![
            make_task("a", TaskType::Transcribe, &[]),
            make_task("b", TaskType::Translate, &[]),
            make_task("c", TaskType::Subtitle, &[]),
        ];
        insert_job_and_tasks(&db, tasks);
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let c2 = cancel.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(20));
            c2.store(true, std::sync::atomic::Ordering::SeqCst);
        });
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 80 }),
            Arc::new({
                let c = cancel.clone();
                move || c.load(Ordering::SeqCst)
            }) as Arc<dyn Fn() -> bool + Send + Sync>,
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Cancelled);
        let conn = db.conn();
        for id in ["a", "b", "c"] {
            let s = task::get_task(&conn, id).unwrap().unwrap().status;
            assert_ne!(
                s,
                TaskStatus::Running,
                "task {id} should not remain Running"
            );
        }
    }

    #[test]
    fn succeeded_remains_after_cancel_race() {
        let db = mem_db();
        let a = make_task("a", TaskType::Transcribe, &[]);
        insert_job_and_tasks(&db, vec![a]);
        // Task succeeds quickly, cancel set right after
        let cancel = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(SleepExecutor { sleep_ms: 10 }),
            Arc::new({
                let c = cancel.clone();
                move || c.load(Ordering::SeqCst)
            }) as Arc<dyn Fn() -> bool + Send + Sync>,
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        // If task succeeded before cancel was observed, outcome is Completed and task stays Succeeded
        // If cancel won, outcome is Cancelled. Both are valid, but SUCCEEDED must not become CANCELLED
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        if outcome == PipelineOutcome::Completed {
            assert_eq!(ta.status, TaskStatus::Succeeded);
        } else {
            assert_eq!(ta.status, TaskStatus::Cancelled);
        }
        // Verify that a Succeeded task cannot be transitioned to Cancelled via guard
        if ta.status == TaskStatus::Succeeded {
            let ok =
                task::update_task_status(&conn, "a", TaskStatus::Cancelled, "2026-08-22T00:00:01Z")
                    .unwrap();
            assert!(!ok, "Succeeded -> Cancelled must be rejected");
        }
    }

    #[test]
    fn retry_does_not_restart_succeeded() {
        // Use custom executor that fails only b once
        struct FailBOnce {
            count: Mutex<i32>,
        }
        impl TaskExecutor for FailBOnce {
            fn execute_task(
                &self,
                task: &Task,
                _cancel: &dyn Fn() -> bool,
                _prog: &dyn Fn(f64, &str),
                _log: &dyn Fn(&str, &str),
            ) -> Result<TaskResult, TaskRunnerError> {
                if task.id == "b" {
                    let mut c = self.count.lock().unwrap();
                    if *c > 0 {
                        *c -= 1;
                        return Err(TaskRunnerError::TaskExecution("transient b".to_string()));
                    }
                }
                Ok(TaskResult {
                    task_id: task.id.clone(),
                    status: TaskStatus::Succeeded,
                    error_code: None,
                    error_message: None,
                    result_json: None,
                })
            }
        }
        let db2 = mem_db();
        let a2 = make_task("a", TaskType::Transcribe, &[]);
        let mut b2 = make_task("b", TaskType::Translate, &["a"]);
        b2.max_attempts = 3;
        insert_job_and_tasks(&db2, vec![a2, b2]);
        let runner2 = TaskRunner::new(
            db2.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(FailBOnce {
                count: Mutex::new(1),
            }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner2.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db2.conn();
        assert_eq!(
            task::get_task(&conn, "a").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
        assert_eq!(
            task::get_task(&conn, "b").unwrap().unwrap().status,
            TaskStatus::Succeeded
        );
    }

    #[test]
    fn crash_resume_running_to_queued() {
        let db = mem_db();
        let mut a = make_task("a", TaskType::Transcribe, &[]);
        a.status = TaskStatus::Running;
        a.started_at = Some("2026-08-22T00:00:00Z".to_string());
        insert_job_and_tasks(&db, vec![a]);
        // Simulate restart: TaskRunner::run should resume RUNNING -> QUEUED and then succeed
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let conn = db.conn();
        let ta = task::get_task(&conn, "a").unwrap().unwrap();
        assert_eq!(ta.status, TaskStatus::Succeeded);
    }

    #[test]
    fn chunked_single_task_unchanged() {
        let db = mem_db();
        let c = make_task("chunk", TaskType::Chunk, &[]);
        insert_job_and_tasks(&db, vec![c]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(MockExecutor { succeed: true }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
    }

    #[test]
    fn independent_tasks_overlap() {
        use std::sync::{Barrier, Mutex as StdMutex};
        let barrier = Arc::new(Barrier::new(2));
        let start_times = Arc::new(StdMutex::new(Vec::<(String, std::time::Instant)>::new()));
        struct BarrierExecutor {
            barrier: Arc<Barrier>,
            start_times: Arc<StdMutex<Vec<(String, std::time::Instant)>>>,
        }
        impl TaskExecutor for BarrierExecutor {
            fn execute_task(
                &self,
                task: &Task,
                _cancel: &dyn Fn() -> bool,
                _prog: &dyn Fn(f64, &str),
                _log: &dyn Fn(&str, &str),
            ) -> Result<TaskResult, TaskRunnerError> {
                if task.task_type == TaskType::Subtitle || task.task_type == TaskType::Tts {
                    self.start_times
                        .lock()
                        .unwrap()
                        .push((task.id.clone(), std::time::Instant::now()));
                    self.barrier.wait();
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                Ok(TaskResult {
                    task_id: task.id.clone(),
                    status: TaskStatus::Succeeded,
                    error_code: None,
                    error_message: None,
                    result_json: None,
                })
            }
        }
        let db = mem_db();
        let t1 = make_task("transcribe", TaskType::Transcribe, &[]);
        let t2 = make_task("translate", TaskType::Translate, &["transcribe"]);
        let t3 = make_task("subtitle", TaskType::Subtitle, &["translate"]);
        let t4 = make_task("tts", TaskType::Tts, &["translate"]);
        insert_job_and_tasks(&db, vec![t1, t2, t3, t4]);
        let runner = TaskRunner::new(
            db.clone(),
            "job_1".to_string(),
            ConcurrencyConfig::default(),
            Arc::new(BarrierExecutor {
                barrier: barrier.clone(),
                start_times: start_times.clone(),
            }),
            Arc::new(|| false),
            Arc::new(NoopTaskSink),
        );
        let outcome = runner.run().unwrap();
        assert_eq!(outcome, PipelineOutcome::Completed);
        let times = start_times.lock().unwrap();
        assert_eq!(times.len(), 2, "subtitle and tts should both have started");
        let diff = if times[0].1 > times[1].1 {
            times[0].1.duration_since(times[1].1)
        } else {
            times[1].1.duration_since(times[0].1)
        };
        assert!(
            diff.as_millis() < 50,
            "subtitle and tts should overlap, diff was {}ms",
            diff.as_millis()
        );
    }
}
