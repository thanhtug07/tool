# UPGRADE IMPLEMENTATION PLAN - AI Video Localization Studio

> Generated: 2026-08-22 | Based on: Full repository audit + UPGRADE_MASTER_PLAN.md v2
> Status: IMPLEMENTED 2026-08-22 — Phase 0, 0.5, 1, 2, 3 done; Phase 4 mock benchmark measured (real 5min video pending)
> Revision: v2 - incorporates 13 architectural fixes from review.
> Check: [x] Phase 0.5 contract, [x] Phase 0 migration, [x] Phase 1 TaskRunner, [x] Phase 2 dumb renderer, [x] Phase 3 worker tasks, [~] Phase 4 benchmark (mock done, real pending)

---

## 1. EXECUTIVE SUMMARY

### Confirmed Root Causes of Sequential Behavior

| Problem                     | Root Cause                                                                                                     | Classification            |
| --------------------------- | -------------------------------------------------------------------------------------------------------------- | ------------------------- |
| Classic pipeline sequential | Frontend submits stages one at a time. Each stage is a separate job; JobService FIFO enforces 1-job-at-a-time. | REAL EXECUTION BOTTLENECK |
| Logs appear sequential      | Worker stdout goes to Rust logger only; frontend receives only /v1/progress poll events (500ms).               | OBSERVABILITY BOTTLENECK  |
| Progress feels frozen       | pipelineProgress() divides equally among stages. When STT dominates, progress stalls.                          | OBSERVABILITY BOTTLENECK  |
| Independent stages wait     | Frontend only submits next stage after previous succeeds. Subtitle/tts/logo are independent but sequential.    | REAL EXECUTION BOTTLENECK |

### Architecture Verdict (REVISED)

No rewrite needed. Four targeted changes:

1. **Rust Pipeline Orchestrator** owns dependency graph - frontend only sends AUTOMATE
2. Task-level concurrency layer within a single job (Rust-side)
3. Observability improvements (per-task progress, event tagging)
4. tasks table for task-level state (single source of truth)

### Critical Design Principles

- **Rust is the orchestrator.** Frontend does NOT know or manage the dependency graph.
- **Task state machine is explicit.** BLOCKED state exists for dependency failures.
- **Cancellation propagates top-down.** Race conditions are defined, not left implicit.
- **Retry is task-scoped.** Failed task retries; dependency failure blocks dependents.
- **Resume is deterministic.** RUNNING->QUEUED; SUCCEEDED is immutable.
- **Idempotency via artifacts.** If artifact exists and valid, skip execution.

---

## 2. REPOSITORY AUDIT

### 2.1 Frontend

| Component       | File                                       | Key Finding                                                                                               |
| --------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| StudioWorkspace | src/workspace/StudioWorkspace.tsx:740-860  | submitStage submits one stage at a time. useEffect at 782-790 only triggers next after previous succeeds. |
| automation.ts   | src/pages/Automation/automation.ts:296-307 | automationPipelineSteps() builds flat array. pipelineProgress() at 406-423 averages equally.              |
| LiveLog.tsx     | src/pages/Automation/LiveLog.tsx:96-122    | Subscribes to job:log events only. No chunk-level interleaving.                                           |
| logHelpers.ts   | src/pages/Automation/logHelpers.ts         | Pure helpers. backfillFromJobs restores history from DB.                                                  |
| jobs.tsx        | src/stores/jobs.tsx:39-134                 | JobsProvider: 3s poll + event merge. Optimistic merge on job:status.                                      |
| events.ts       | src/api/events.ts:14-30                    | Subscribes to job:status and job:log Tauri events.                                                        |

### 2.2 Rust Core

| Component      | File                       | Key Finding                                                                       |
| -------------- | -------------------------- | --------------------------------------------------------------------------------- |
| PipelineRunner | pipeline_runner.rs:263-340 | run_stage() spawns HTTP on thread, polls 500ms. Each job type runs independently. |
| JobService     | job_service.rs:702-722     | worker_loop() FIFO queue, one job at a time.                                      |
| WorkerManager  | worker_manager.rs:681-707  | read_into_channel() reads stdout/stderr. forward() logs to Rust logger only.      |
| WorkerClient   | worker_client.rs           | HTTP/1.1 on TcpStream. get_progress() polls /v1/progress.                         |
| JobRepo        | db/repo/job.rs             | SQLite CRUD. No tasks table.                                                      |

### 2.3 Python Worker

| Component        | File                              | Key Finding                                                                                  |
| ---------------- | --------------------------------- | -------------------------------------------------------------------------------------------- |
| pipeline.py      | api/pipeline.py:58-81,1199-1224   | _cancel_scope() per-job CancellationToken. job_progress() returns progress + drained events. |
| job.py           | core/job.py:53-123                | CancellationToken with lock-protected progress/message/events FIFO queue.                    |
| chunk_service.py | services/chunk_service.py:320-417 | ChunkScheduler with ThreadPoolExecutor(max_concurrency=4).                                   |

### 2.4 Infrastructure

| Component  | Key Finding                                            |
| ---------- | ------------------------------------------------------ |
| Migrations | 8 migrations (v1-v8). No tasks table.                  |
| Schemas    | 8 schemas. No task-level schema.                       |
| CI         | Worker: smoke import only, no pytest in CI.            |
| Tests      | Frontend: 28 files. Rust: 100+. Worker: 10 unit files. |

---

## 3. DOCUMENTATION vs CODE DISCREPANCIES

| Claim                             | Source                   | Classification  |
| --------------------------------- | ------------------------ | --------------- |
| 1 worker 1 job (FIFO)             | UPGRADE_MASTER_PLAN 0    | CONFIRMED       |
| per_job.max_parallel_tasks = 2-3  | UPGRADE_MASTER_PLAN 0    | NOT IMPLEMENTED |
| PipelineTask model with dependsOn | UPGRADE_MASTER_PLAN 1.1  | NOT IMPLEMENTED |
| SQLite tasks table                | UPGRADE_MASTER_PLAN 1.2  | NOT IMPLEMENTED |
| ChunkScheduler ThreadPoolExecutor | SYSTEM_ARCHITECTURE 5.3b | CONFIRMED       |
| Live progress poll 500ms          | SYSTEM_ARCHITECTURE 5.1  | CONFIRMED       |
| Stage-level retry 3x              | SYSTEM_ARCHITECTURE 5.1  | CONFIRMED       |
| DATABASE.md outdated              | SYSTEM_ARCHITECTURE 14   | CONFIRMED       |
| API.md missing routes             | SYSTEM_ARCHITECTURE 14   | CONFIRMED       |
| glossaryFingerprint unused        | SYSTEM_ARCHITECTURE 13   | CONFIRMED       |
| WorkflowController no-op          | SYSTEM_ARCHITECTURE 13   | CONFIRMED       |
| media:// handler not active       | SYSTEM_ARCHITECTURE 13   | CONFIRMED       |

---

---

## 4. CURRENT ARCHITECTURE

### 4.1 Execution Model

```
CLASSIC MODE (current):
Frontend: submitStage() -> submit next on succeed
  stages: [transcribe, translate, subtitle, tts, logo, render]
  Submission: SEQUENTIAL (one at a time)
Rust JobService: FIFO queue, 1 job at a time
PipelineRunner: one JobRunner per job type
  Each stage: spawn thread -> HTTP -> poll 500ms
Worker: single FastAPI endpoint per stage

CHUNKED MODE (current):
Frontend: submits single chunk job
Rust: run_chunk() -> single POST -> poll 500ms
Worker: ChunkScheduler with ThreadPoolExecutor(4)
  Each chunk: slice -> STT -> translate -> TTS
```

### 4.2 Concurrency Analysis

INTER-JOB CONCURRENCY:

- max_concurrent_jobs = 1 (FIFO queue in job_service.rs:702-722)
- Confirmed by: worker_loop() processes one job_id at a time

INTRA-JOB CONCURRENCY:

- Classic mode: NONE (each stage is a separate job)
- Chunked mode: ThreadPoolExecutor(max_concurrency=4) within single job
- Source: chunk_service.py:320-417 ChunkScheduler

### 4.3 State Model

```
SQLite jobs table (only state source):
  id, project_id, type, status, progress, stage,
  error_code, error_message, error_log,
  params_json, retry_count, cancel_requested,
  created_at, updated_at, started_at, finished_at

Job lifecycle:
  Queued -> Running -> Succeeded
                    -> Failed (retry_count < max -> re-queue)
                    -> Cancelled

No tasks table exists. Each stage = 1 job. Progress is per-job only.
```

---

## 5. ACTUAL RUNTIME FLOWS

### FLOW A: Classic Automation

```
1. User clicks AUTOMATE
2. handleAutomate() -> automationPipelineSteps(dubAudio, logoRemoval, false)
   Returns: [transcribe, translate, subtitle, tts?, logo?, render]
3. startPipelineWithSteps(steps, options) -> PipelinePlan
4. submitStage(steps[0]) -> IPC job.submit -> JobService.submit()
   -> SQLite INSERT -> FIFO enqueue
5. worker_loop() -> pop job_id -> process()
   -> transition(Running) -> emit job:status
6. PipelineRunner::run() -> match JobType::Transcribe -> run_transcribe()
   -> run_stage(client, job, ctx, window, call)
     -> spawn thread: WorkerClient HTTP POST endpoints
     -> main thread: poll ctx.is_cancelled() + client.get_progress() every 500ms
     -> emit progress via ctx.progress -> report_progress -> persist + emit job:status
   -> verify artifact (E_ARTIFACT_MISSING guard)
   -> write transcript.json
7. Job finishes -> Succeeded -> emit job:status
8. Frontend useEffect (782-790): detects pendingIndex, submits next stage
9. Repeat for each stage sequentially
```

### FLOW B: Chunked Automation

```
1. User clicks AUTOMATE with chunked=true
2. automationPipelineSteps(dubAudio, logoRemoval, true) -> [chunk]
3. submitStage("chunk") -> IPC job.submit(type=Chunk)
4. PipelineRunner::run_chunk():
   a. Extract audio -> verify WAV
   b. POST /v1/automation/chunked (ChunkedAutomationRequest)
   c. Worker: ChunkScheduler -> ThreadPoolExecutor(4) processes chunks
      - Each chunk: slice_audio -> STT -> translate -> TTS
      - Events via token.set_event() -> FIFO queue
   d. Rust polls /v1/progress every 500ms:
      - Gets aggregated progress (0.05 -> 0.9)
      - Gets drained events array
      - Forwards events as job:log to frontend
   e. Verify merged artifacts
   f. POST /v1/render -> verify output
   g. POST /v1/automation/finalize -> final validation + cleanup
```

### FLOW C: Realtime Logging

```
WORKER -> RUST:
  Worker stdout (JSON) -> read_into_channel() -> WorkerLine channel
  -> Supervisor::forward() -> log::info!("[worker:stdout] ...") [RUST LOGGER ONLY]

  Worker CancellationToken.set_event() -> _events FIFO queue
  -> /v1/progress/{job_id} -> token.drain_events() -> JSON response
  -> PipelineRunner::run_stage() reads events from response
  -> ctx.log() -> JobService::emit_log() -> job:log event

FRONTEND:
  Tauri event "job:log" -> onJobLog handler -> LiveLog setEntries()
  -> appendLogEntry() -> React render

KEY INSIGHT: Worker stdout logs do NOT reach frontend.
Only /v1/progress events reach frontend via job:log.
```

### FLOW D: Progress

```
WORKER:
  CancellationToken.set_progress(progress, stage, message)
  -> stored in _progress, _stage, _message (single values, overwritten)

RUST:
  /v1/progress/{job_id} poll every 500ms (PROGRESS_POLL_INTERVAL)
  -> client.get_progress() -> {progress, stage, message, events}
  -> Map to stage window: win_start + progress * (win_end - win_start)
  -> report_progress() -> persist to jobs.progress + emit job:status

FRONTEND:
  job:status event -> JobsProvider merge -> deriveStages()
  pipelineProgress(stages) -> equal-weight average across all stages
  -> displayed as percentage + progress bar
```

### FLOW E: Cancellation

```
User clicks Cancel:
  -> JobService.cancel(job_id):
    - Queued: immediately transition to Cancelled
    - Running: set cancel_flags[job_id] = true + persist cancel_requested

PipelineRunner:
  -> ctx.is_cancelled() check between stages
  -> client.cancel_job(job_id) -> POST /v1/jobs/{job_id}/cancel
  -> Worker: token.cancel() -> sets threading.Event

Worker subprocess:
  -> _reap thread: polls cancel.is_cancelled() -> _kill_tree()
  -> Windows: taskkill /T /F
  -> POSIX: SIGTERM -> grace -> SIGKILL
```

### FLOW F: Retry

```
Stage-level (Rust, pipeline_runner.rs:352-392):
  -> run_stage_retryable(): up to 3 attempts
  -> Retries: E_TTS_FAILED, E_API_ERROR, E_API_RATE_LIMIT
  -> Backoff: 2s * attempt between retries
  -> Cancel check between retries

Job-level (Rust, job_service.rs:546-575):
  -> JobRunError::Transient -> retry with backoff 1s/5s/30s, max 3
  -> Excludes Cancelled and Permanent errors

Worker-level (Python, per-service):
  -> edge-tts: 3x1.5s retry for NoAudioReceived
  -> Gemini: 3x backoff (1,2,4)s for 429/5xx
  -> QualityGate: 3x backoff (1,5,30)s for transient errors
```

### FLOW G: Resume After Crash

```
On startup (JobService::resume, job_service.rs:382-428):
  -> list_queued() -> re-enqueue into FIFO
  -> list_running():
    - If cancel_requested: transition to Cancelled
    - Else: transition back to Queued (resume from last stage)
  -> worker_loop picks up and processes normally
```

### FLOW H: Artifact Verification

```
EVERY stage result goes through:
  -> worker validates its own output before answering HTTP 200
  -> Rust runner verifies artifact file exists on disk AND is non-empty:
    - audio: pipeline_runner.rs:533-542
    - subtitle output: pipeline_runner.rs:1114-1120
    - voice track: pipeline_runner.rs:937-948
    - rendered video: pipeline_runner.rs:1255-1263
    - logo removal: pipeline_runner.rs:1009-1017
    - audio processing: pipeline_runner.rs:1062-1070
  -> If file missing or empty: E_ARTIFACT_MISSING (Permanent error)
  -> This invariant is NEVER relaxed
```

---

## 6. CONCURRENCY AUDIT

### 6.1 The Core Question

ARE WE WAITING FOR REAL EXECUTION OR JUST THE PRESENTATION?

ANSWER: BOTH.

REAL EXECUTION BOTTLENECK:

- Classic pipeline: each stage is a separate job in FIFO queue
- Frontend only submits next stage after previous succeeds
- Independent stages (subtitle, tts, logo) could run concurrently but do not

OBSERVABILITY BOTTLENECK:

- Worker stdout goes to Rust logger only, not to frontend
- Frontend only sees /v1/progress poll events (500ms interval)
- Logs appear sequential because only one aggregated progress source
- Progress appears frozen because pipelineProgress() averages equally

### 6.2 What Needs Parallelization

STAGE-LEVEL (Classic mode):

```
transcribe -> translate -> subtitle -> tts -> logo -> render
                       |
                       +-> subtitle -+
                       +-> tts      -+-> render
                       +-> logo  ---+
```

After translate: subtitle, tts, logo are independent. These 3 could run concurrently.

CHUNK-LEVEL (Already parallel):

- ThreadPoolExecutor(max_concurrency=4) in chunk_service.py
- Each chunk processes independently. Already working correctly.

### 6.3 Proposed Solution

RUST PIPELINE ORCHESTRATOR with TASK-LEVEL CONCURRENCY:

- Frontend sends only: {project_id, mode, options}
- Rust builds dependency graph internally
- Rust spawns independent tasks concurrently via TaskRunner
- Progress/events flow back via Tauri events
- Frontend is a DUMB RENDERER of task state

---

## 7. LOGGING AUDIT

### 7.1 Current Flow

```
Worker stdout -> read_into_channel() -> Rust logger (log::info!)
Worker events -> /v1/progress -> PipelineRunner -> ctx.log() -> job:log event
Frontend: subscribes to job:log -> LiveLog
```

### 7.2 Gap Analysis

WHAT REACHES FRONTEND:

- /v1/progress events (aggregated, 500ms poll)
- job:status events (job lifecycle changes)

WHAT DOES NOT REACH FRONTEND:

- Worker stdout (only goes to Rust logger)
- Chunk-level progress within multi-chunk jobs
- Detailed stage progress within chunk processing
- Log timestamps from worker (only Rust-side timestamps)

### 7.3 Proposed Solution

PER-TASK EVENT STREAMING:

- Worker sends events with task_id tag
- Rust forwards task-level events to frontend
- Frontend shows per-task progress and logs
- LiveLog supports filtering by task_id

---

## 8. PROGRESS AUDIT

### 8.1 Current Implementation

```
Worker side (pipeline.py):
  token.set_progress(progress, stage, message)
  Single values, overwritten on each call

Rust side (pipeline_runner.rs):
  Polls /v1/progress every 500ms
  Maps to stage window: win_start + progress * (win_end - win_start)
  Persists to jobs.progress + emits job:status

Frontend (automation.ts):
  pipelineProgress(stages) -> equal-weight average
  Each stage has equal weight regardless of actual duration
```

### 8.2 Problem

- STT stage: can take 5-30 minutes for long audio
- Other stages: 30s-2min
- Equal weighting: STT progress 0-100% but overall progress only shows as 0-16%
- User sees progress appear to stall during STT

### 8.3 Proposed Solution

WEIGHTED PROGRESS (UX estimation, not physical execution percentage):

- Each stage has configurable weight based on typical duration
- Progress reflects actual time spent vs estimated
- Per-task progress bar for active task
- Overall job progress aggregates weighted task progress
- NOTE: Weight is UX estimation only. Actual speedup must be benchmarked.

---

## 8.5 TASK STATE MACHINE (NEW)

### 8.5.1 States

```
QUEUED     - Task not ready to run: dependencies not satisfied, or retry backoff
READY      - All dependencies satisfied AND no backoff; waiting for concurrency slot
RUNNING    - Task is actively executing
SUCCEEDED  - Task completed successfully (immutable)
FAILED     - Task failed permanently (terminal)
CANCELLED  - Task was cancelled (terminal)
BLOCKED    - A dependency failed permanently; this task cannot run
```

STATE SEMANTICS:
QUEUED vs READY:
QUEUED = cannot run yet (dependency pending, or retry backoff timer)
READY = CAN run now, just waiting for resource/slot
This distinction helps debugging and UI display.

### 8.5.2 State Transitions

```
QUEUED -> READY         (when all depends_on tasks are SUCCEEDED)
QUEUED -> BLOCKED       (when a depends_on task becomes permanently FAILED)
READY -> RUNNING        (when concurrency slot available)
RUNNING -> SUCCEEDED    (task completed successfully)
RUNNING -> QUEUED       (transient failure, retry available, retry_count < max)
RUNNING -> FAILED       (permanent failure OR retry exhausted)
RUNNING -> CANCELLED    (cancel requested while running)
BLOCKED -> QUEUED       (if failed dependency is retried and succeeds)
```

### 8.5.3 Terminal States

SUCCEEDED, FAILED, CANCELLED are terminal.
BLOCKED is semi-terminal (unblocked only if dependency retry succeeds).
QUEUED after transient failure is NOT terminal (will be retried).

### 8.5.4 Failure Propagation

When task T fails:

1. Classify error: transient vs permanent
2. If transient AND retry_count < max_retries:
   - T.retry_count += 1
   - T -> QUEUED (re-queued for retry)
   - Dependents: NO CHANGE (still waiting)
3. If permanent OR retry_count >= max_retries:
   - T -> FAILED (terminal)
   - For all tasks D where T in D.depends_on:
     - If D is QUEUED or READY -> D -> BLOCKED
     - If D is RUNNING -> do NOT auto-cancel (let running task finish or fail)
     - If D is SUCCEEDED -> already done, no change
4. Job state aggregation (DEFINED):
   Job QUEUED -> tasks created, TaskRunner not started
   Job RUNNING -> at least one task RUNNING/READY/QUEUED
   Job SUCCEEDED -> ALL tasks SUCCEEDED
   Job FAILED -> ANY task FAILED (terminal) AND no retry pending
   Job CANCELLED -> cancel requested AND all non-terminal tasks cancelled

NOTE: Job does NOT have BLOCKED state. BLOCKED is task-only.
When all non-succeeded tasks are BLOCKED and no retry possible -> Job FAILED.

IMPORTANT: BLOCKED is ONLY created when dependency reaches terminal FAILED.
Transient failure + retry does NOT create BLOCKED dependents.

### 8.5.5 BLOCKED Resolution

BLOCKED tasks are resolved when:

- A permanently failed dependency is manually retried and succeeds (future V2)
- The pipeline is reset/restarted from a checkpoint
- Manual intervention (future: retry button per task)

### 8.5.6 State Invariants

Each state has required invariants for validation:

SUCCEEDED:
progress = 1.0, finished_at != null, result_json != null

RUNNING:
started_at != null, finished_at == null

FAILED:
error_code != null, error_message != null, finished_at != null

CANCELLED:
cancel_requested = 1, finished_at != null

BLOCKED:
exists dependency with status = FAILED (permanent only)

QUEUED (retry):
retry_count > 0, error_code != null (retains failure info)

---

---

## 9. TARGET ARCHITECTURE (REVISED)

### 9.1 Architecture Overview

```
                    +---------------------+
                    |      Frontend       |
                    |                     |
                    |  START AUTOMATION   |
                    |  CANCEL             |
                    |  VIEW TASKS         |
                    +----------+----------+
                               |
                               | IPC: {project_id, mode, options}
                               v
                    +---------------------+
                    |   Rust Orchestrator |
                    |                     |
                    | Pipeline Orchestrator|
                    | builds dependency   |
                    | graph internally    |
                    +----------+----------+
                               |
                               v
                    +---------------------+
                    |     TaskRunner      |
                    |                     |
                    | dependency DAG      |
                    | ready queue         |
                    | concurrency limit   |
                    | cancellation        |
                    | retry               |
                    +----------+----------+
                               |
             +-----------------+-----------------+
             v                 v                 v
        Transcribe           TTS              Subtitle
             |                 |                 |
             +-----------------+-----------------+
                               v
                            Render
                               |
                               v
                          Final Artifact
```

### 9.2 Dependency Graph (Owned by Rust)

```
STANDARD GRAPH (pending code audit of logo dependency):
  transcribe -> translate -> subtitle -> render
                        |-> tts      -> render
                        |-> logo?    -> render  (audit required: does logo depend on translate output?)

AUDIT NOTE: Logo removal may only need the input video, not translate output.
If so, logo can start immediately after transcribe (or even in parallel).
Agent must verify actual data dependency in pipeline_runner.rs before locking DAG.

CHUNKED GRAPH:
  chunk (single job, ThreadPoolExecutor internally)

The dependency graph is BUILT IN RUST, not in frontend.
Frontend sends: {project_id, mode: "classic"|"chunked", options}
Rust decides: which tasks, what dependencies, what concurrency.
```

### 9.3 Frontend Simplified

```
Frontend ONLY does:
1. Send AUTOMATE command (no stage knowledge)
2. Receive task:created, task:started, task:progress, task:succeeded, task:failed, task:cancelled
3. Display task list with status and progress
4. Send CANCEL command

Frontend does NOT:
- Know the dependency graph
- Submit individual stages
- Decide execution order
- Manage concurrency
```

### 9.4 Task Schema (Production-Ready)

```
CREATE TABLE tasks (
  id              TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_type       TEXT NOT NULL,        -- transcribe, translate, subtitle, tts, logo, render, chunk
  stage           TEXT NOT NULL,        -- display stage name
  status          TEXT NOT NULL DEFAULT 'queued',
                    -- QUEUED, READY, RUNNING, SUCCEEDED, FAILED, CANCELLED, BLOCKED
  progress        REAL DEFAULT 0.0,     -- 0.0 to 1.0
  depends_on      TEXT DEFAULT '[]',    -- JSON array of task IDs
  params_json     TEXT,                 -- task-specific parameters
  input_fingerprint TEXT,                -- sha256(input + params + model + pipeline_version)
  result_json     TEXT,                 -- task output metadata
  error_code      TEXT,                 -- structured error code
  error_message   TEXT,                 -- human-readable error
  retry_count     INTEGER DEFAULT 0,
  max_attempts    INTEGER DEFAULT 3,
  cancel_requested INTEGER DEFAULT 0,   -- 0 or 1
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  started_at      DATETIME,
  finished_at     DATETIME
);

CREATE INDEX idx_tasks_job_id ON tasks(job_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_job_status ON tasks(job_id, status);
```

### 9.5 TaskRunner Design

```
struct TaskRunner {
    job_id: Uuid,
    db: Database,
    max_concurrency: usize,        // global limit: 3
    per_type_limits: HashMap<String, usize>,  // STT=1, TTS=2, Render=1
    cancel_token: CancellationToken,
}

impl TaskRunner {
    async fn run(&self) -> Result<()> {
        // 1. Load tasks from DB
        // 2. Validate dependency graph (cycle detection, existence check)
        // 3. Initialize: tasks with empty depends_on -> READY
        // 4. Loop:
        //    a. Get READY tasks (respecting per-type concurrency limits)
        //    b. Spawn READY tasks up to limits
        //    c. Wait for any task to complete
        //    d. On completion: update DB, check dependents
        //    e. If all tasks terminal -> break
        // 5. Return overall result
    }

    fn validate_dag(&self, tasks: &[Task]) -> Result<()> {
        // - Check all depends_on references exist
        // - Detect cycles (topological sort)
        // - Reject duplicate task IDs
    }

    fn check_dependents(&self, completed_task: &Task) -> Vec<Task> {
        // For each task T where completed_task in T.depends_on:
        //   If all T.depends_on are SUCCEEDED -> T -> READY
        //   If any T.depends_on is FAILED -> T -> BLOCKED
    }
}
```

### 9.6 Rust Sync/Async Boundary (REVISED)

```
CURRENT ARCHITECTURE:
  JobService (sync, std::thread) -> worker_loop -> PipelineRunner (sync)
  PipelineRunner spawns threads for HTTP polling

PROPOSED BOUNDARY:
  JobService stays sync (no rewrite)
  TaskRunner uses tokio::spawn INSIDE PipelineRunner::run()
  The sync->async boundary:
    - PipelineRunner::run() is called from sync context
    - It calls tokio::runtime::Handle::block_on() to run TaskRunner
    - TaskRunner uses JoinSet for concurrent tasks
    - Each task does sync HTTP polling via spawn_blocking()

WHY:
  - JobService/FIFO queue stays unchanged
  - TaskRunner is isolated async island
  - No architecture contamination
  - Agent must verify runtime model before choosing JoinSet vs rayon

AGENT MUST AUDIT:
  - Current tokio runtime setup in main.rs
  - Whether Tauri uses multi-threaded or current-thread runtime
  - Impact of block_on() on existing code
  - Choose: JoinSet, rayon, std::thread, or crossbeam
```

### 9.7 Worker Integration (No New Endpoint)

```
CURRENT:
  /v1/progress/{job_id} -> {progress, stage, message, events[]}

PROPOSED (minimal change):
  /v1/progress/{job_id} -> {
    progress, stage, message, events[],
    tasks: [
      {task_id, progress, stage, status}
    ]
  }

Rationale: One endpoint, extended with task array.
No new /v1/tasks/ endpoints needed.
Task aggregation happens in Rust TaskRunner, not worker.

Worker side: CancellationToken already supports per-task events.
TaskRunner reads task events from /v1/progress response.
```

---

## 10. IMPLEMENTATION PHASES (REVISED)

### Phase -1: Baseline Benchmark (NEW)

PURPOSE: Measure current sequential pipeline performance before upgrade.

BENCHMARK:
Input: 10-minute video (representative workload)
Measure per stage: transcribe, translate, subtitle, tts, logo, render
Record: total time, per-stage time, resource usage
Save as: docs/BASELINE_BENCHMARK.md

WHY: Without baseline, no way to measure improvement.

### Phase 0.5: Task Contract and State Machine (NEW - BEFORE CODING)

PURPOSE: Lock down all semantics before any code changes.

DELIVERABLE: docs/TASK_ARCHITECTURE.md containing:

- Task state machine (all states, transitions, terminal states)
- Task event format (JSON schema)
- Dependency semantics (depends_on resolution)
- Failure propagation rules
- Retry semantics (task-level only)
- Cancellation propagation (top-down, race conditions)
- Resume semantics (RUNNING->QUEUED, SUCCEEDED immutable)
- Idempotency rules (artifact verification)
- DAG validation rules (cycle detection, existence check)
- Concurrency limits (global + per-type)

REVIEW: Agent must present this document for approval before proceeding.

### Phase 0.75: Runtime and Repository Audit (NEW - REQUIRED)

PURPOSE: Verify all assumptions before coding. This phase prevents agent from making incorrect architectural decisions.

AUDIT CHECKLIST (agent must complete ALL):

1. JobService actual async/sync model - verify tokio runtime in main.rs
2. PipelineRunner actual signature - verify async/block_on boundary
3. Worker cancellation architecture - verify token.cancel() propagation
4. Worker task event architecture - verify CancellationToken event FIFO
5. Artifact generation paths - verify all output file locations
6. Current retry classification - verify which errors are transient vs permanent
7. Existing IPC commands - verify all Tauri invoke commands
8. Existing frontend automation state - verify StudioWorkspace state management
9. Existing migration conventions - verify migration numbering and patterns
10. Logo removal actual dependencies - verify if logo depends on translate output

DELIVERABLE: Audit results documented. Any discrepancies with plan noted and resolved.

### Phase 0: Database Migration (No behavior change)

TASKS:

- Create migration v9: add tasks table (with full schema from Section 9.4)
- Add task CRUD operations to JobRepo
- Update schemas: task.json
- Add tests for task operations
- IMPLEMENT: Atomic task creation (see below)

ATOMIC TASK CREATION:
PipelineOrchestrator.create_tasks() MUST use a single SQLite transaction:
BEGIN TRANSACTION
INSERT job (...)
INSERT task 1 (...)
INSERT task 2 (...)
...
INSERT task N (...)
COMMIT
If any INSERT fails -> entire transaction rolls back.
No partial state. TaskRunner only starts after COMMIT.

FILES:

- src-tauri/src/db/migrations.rs (add migration v9)
- src-tauri/src/db/repo/task.rs (new file)
- schemas/task.json (new file)
- src-tauri/src/db/repo/job.rs (add task relationships)

VERIFICATION:

- cargo check passes
- cargo test passes
- Database migration runs cleanly on empty DB
- Database migration runs cleanly on existing DB with data
- Atomic creation: crash during INSERT -> no partial tasks

### Phase 1: Rust TaskRunner (Core + Cancel/Retry/Resume)

TASKS:

- Create TaskRunner struct with dependency graph
- Implement DAG validation (cycle detection, existence check)
- Implement concurrent task execution with ConcurrencyConfig
- Add task-level progress tracking
- Implement CANCELLATION (2-phase: signal -> confirm)
- Implement RETRY (transient vs permanent, max_attempts)
- Implement RESUME (RUNNING->QUEUED, artifact fingerprint check)
- Integrate with existing PipelineRunner
- Handle sync/async boundary correctly

FILES:

- src-tauri/src/services/task_runner.rs (new file)
- src-tauri/src/services/pipeline_runner.rs (integrate TaskRunner)
- src-tauri/src/services/job_service.rs (update to use TaskRunner)

VERIFICATION:

- cargo check passes
- cargo test passes
- DAG validation catches cycles
- DAG validation catches missing dependencies
- Concurrency limits respected
- Existing classic pipeline still works (sequential fallback)

### Phase 2: Frontend Task Display (SIMPLIFIED)

TASKS:

- Update IPC: send {project_id, mode, options} (no stage list)
- Add task list display (subscribe to task:created/started/progress/succeeded/failed/cancelled)
- Update LiveLog to show per-task logs
- Update progress to use weighted stages
- Add task filtering to log view

FILES:

- src/workspace/StudioWorkspace.tsx (simplify submitStage)
- src/pages/Automation/LiveLog.tsx (add task display)
- src/pages/Automation/logHelpers.ts (add task-aware helpers)
- src/api/events.ts (subscribe to task events)

FEATURE FLAG:
automation_orchestrator_v2 = false (default OFF)
When OFF: current pipeline execution path
When ON: TaskRunner V2
Rollback: flip flag OFF, no DB migration needed

VERIFICATION:

- npm run typecheck passes
- npm run lint passes
- npm run test passes
- Frontend displays task list correctly
- Frontend does NOT contain dependency logic
- Feature flag OFF = identical to current behavior

### Phase 3: Worker Task Events (MINIMAL)

TASKS:

- Extend /v1/progress response with tasks[] array
- Worker sends task_id tagged events
- Update cancellation to cancel specific tasks

FILES:

- worker/src/api/pipeline.py (extend progress response)
- worker/src/core/job.py (add task_id to events)

VERIFICATION:

- python -c "import src.main" passes (worker/)
- /v1/progress returns task array
- Task events have task_id tag

### Phase 4: Integration and Polish

TASKS:

- End-to-end testing with concurrent stages
- Performance benchmarking
- Bug fixes from testing
- Documentation updates

VERIFICATION:

- All acceptance criteria met
- All existing tests pass
- No regressions

---

## 11. DETAILED TASK SPECIFICATIONS

### 11.1 Database Migration v9

```
CREATE TABLE tasks (
  id              TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_type       TEXT NOT NULL,
  stage           TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'queued',
  progress        REAL DEFAULT 0.0,
  depends_on      TEXT DEFAULT '[]',
  params_json     TEXT,
  result_json     TEXT,
  error_code      TEXT,
  error_message   TEXT,
  retry_count     INTEGER DEFAULT 0,
  max_attempts    INTEGER DEFAULT 3,
  cancel_requested INTEGER DEFAULT 0,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  started_at      DATETIME,
  finished_at     DATETIME
);

CREATE INDEX idx_tasks_job_id ON tasks(job_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_job_status ON tasks(job_id, status);
```

### 11.2 TaskRunner Core Algorithm

```
fn run(&self) -> Result<()> {
    // 1. Load tasks from DB
    let tasks = self.db.get_tasks_by_job(self.job_id)?;

    // 2. Validate DAG
    self.validate_dag(&tasks)?;

    // 3. Initialize ready queue
    let mut ready: VecDeque<Task> = VecDeque::new();
    let mut running: JoinSet<TaskResult> = JoinSet::new();
    let mut completed: HashSet<Uuid> = HashSet::new();
    let mut blocked: HashSet<Uuid> = HashSet::new();

    for task in &tasks {
        if task.depends_on.is_empty() {
            ready.push_back(task.clone());
        }
    }

    // 4. Main loop
    loop {
        // Spawn ready tasks (respecting concurrency limits)
        // IMPORTANT: Skip unavailable tasks, don't break on first failure
        let mut deferred = VecDeque::new();
        while let Some(task) = ready.pop_front() {
            if self.can_spawn(&task, &running) {
                let db = self.db.clone();
                let cancel = self.cancel_token.clone();
                running.spawn(async move {
                    execute_task(db, task, cancel).await
                });
            } else {
                deferred.push_back(task);
            }
        }
        ready = deferred;

        // DEADLOCK DETECTION
        if ready.is_empty() && running.is_empty() {
            let non_terminal: Vec<&Task> = tasks.iter()
                .filter(|t| !matches!(t.status,
                    TaskStatus::Succeeded | TaskStatus::Failed | TaskStatus::Cancelled))
                .collect();
            if non_terminal.is_empty() {
                break;
            } else {
                return Err(PipelineError::SchedulerDeadlock {
                    stuck_tasks: non_terminal.iter().map(|t| t.id).collect(),
                });
            }
        }

        // Wait for any task
        if let Some(result) = running.join_next().await {
            let result = result?;
            completed.insert(result.task_id);

            // Update DB
            self.db.update_task_status(&result.task_id, &result.status)?;

            // Check dependents
            let newly_ready = self.check_dependents(&result, &tasks, &completed, &blocked);
            for task in newly_ready {
                ready.push_back(task);
            }

            // Check if pipeline failed
            if result.status == TaskStatus::Failed && !self.has_remaining_tasks(&tasks, &completed) {
                return Err(PipelineError::TaskFailed(result.task_id));
            }
        }
    }

    Ok(())
}
```

### 11.3 DAG Validation

```
fn validate_dag(&self, tasks: &[Task]) -> Result<()> {
    // 1. Check all depends_on references exist
    let task_ids: HashSet<Uuid> = tasks.iter().map(|t| t.id).collect();
    for task in tasks {
        let deps: Vec<Uuid> = serde_json::from_str(&task.depends_on)?;
        for dep in deps {
            if !task_ids.contains(&dep) {
                return Err(PipelineError::MissingDependency {
                    task_id: task.id,
                    missing_dep: dep,
                });
            }
        }
    }

    // 2. Detect cycles (Kahn's algorithm)
    let mut in_degree: HashMap<Uuid, usize> = HashMap::new();
    let mut adj: HashMap<Uuid, Vec<Uuid>> = HashMap::new();

    for task in tasks {
        in_degree.entry(task.id).or_insert(0);
        let deps: Vec<Uuid> = serde_json::from_str(&task.depends_on)?;
        for dep in deps {
            adj.entry(dep).or_default().push(task.id);
            *in_degree.entry(task.id).or_insert(0) += 1;
        }
    }

    let mut queue: VecDeque<Uuid> = VecDeque::new();
    for (id, &deg) in &in_degree {
        if deg == 0 {
            queue.push_back(*id);
        }
    }

    let mut count = 0;
    while let Some(id) = queue.pop_front() {
        count += 1;
        if let Some(neighbors) = adj.get(&id) {
            for &neighbor in neighbors {
                let deg = in_degree.get_mut(&neighbor).unwrap();
                *deg -= 1;
                if *deg == 0 {
                    queue.push_back(neighbor);
                }
            }
        }
    }

    if count != tasks.len() {
        return Err(PipelineError::CycleDetected);
    }

    // 3. Check for duplicate task IDs
    let mut seen = HashSet::new();
    for task in tasks {
        if !seen.insert(task.id) {
            return Err(PipelineError::DuplicateTaskId(task.id));
        }
    }

    Ok(())
}
```

### 11.4 Cancellation Semantics

```
CANCEL REQUEST FLOW:
  User clicks Cancel
    -> Frontend sends CANCEL via IPC
    -> Rust JobService.cancel(job_id):
       - Set job.cancel_requested = 1
       - If job status == Queued -> transition to Cancelled immediately
       - If job status == Running -> set cancel_flag
    -> TaskRunner checks cancel_token before each task spawn
    -> TaskRunner cancels all RUNNING tasks:
       - For each running task: client.cancel_task(task_id)
       -> Worker: token.cancel() -> threading.Event
       -> Subprocess: taskkill /T /F (Windows) or SIGTERM->SIGKILL (POSIX)
    -> All RUNNING tasks -> CANCELLED
    -> All READY/QUEUED tasks -> CANCELLED
    -> All BLOCKED tasks -> CANCELLED

RACE CONDITION: Task completing + User presses Cancel
  Solution: Atomic status update in DB
    - Task completion: UPDATE tasks SET status='succeeded' WHERE id=X AND status='running'
    - Cancel request: UPDATE tasks SET status='cancelled' WHERE id=X AND status IN ('queued','ready','running')
    - Only one succeeds due to WHERE clause
    - Loser's update affects 0 rows -> safe to ignore

STATE AFTER CANCEL:
  Job: Cancelled
  Tasks: SUCCEEDED (completed before cancel) + CANCELLED (all others)
  No orphaned tasks running.
```

### 11.5 Retry Semantics (REVISED)

```
TASK-LEVEL RETRY ONLY:
  When task T fails:
    1. Classify error: transient or permanent
    2. If transient AND retry_count < (max_attempts - 1):
       - T.retry_count += 1
       - T.status -> QUEUED (re-queued for retry, NOT FAILED)
       - Backoff: 2s * T.retry_count
       - Dependents: NO CHANGE (still waiting, not BLOCKED)
    3. If permanent OR retry_count >= (max_attempts - 1):
       - T.status -> FAILED (terminal)
       - T.error_code, T.error_message set
       - Dependents -> BLOCKED (only now)

  TERMINOLOGY:
    max_attempts = 3 means: attempt 1 + 2 retries = 3 total executions
    retry_count starts at 0, increments on each retry
    retry_count < (max_attempts - 1) means retries available

  KEY RULE: BLOCKED is ONLY created when dependency reaches FAILED (terminal).
  Transient failure + retry does NOT create BLOCKED.

ERROR CLASSIFICATION:
  Transient: E_TTS_FAILED, E_API_ERROR, E_API_RATE_LIMIT, network timeout
  Permanent: E_ARTIFACT_MISSING, E_INVALID_INPUT, E_PERMISSION_DENIED

JOB-LEVEL RETRY (V1 = DISABLED):
  Task retry handles transient failures within pipeline execution.
  Job retry = explicit user action only (not automatic).
  If all tasks fail permanently -> job FAILED -> user decides to retry.
```

### 11.6 Crash/Resume Semantics

```
ON RESTART (after crash):
  1. Load all tasks for incomplete jobs
  2. Apply state transitions:
     - SUCCEEDED -> keep (immutable, artifact verification will confirm)
     - RUNNING -> QUEUED (safe to re-execute, idempotency via artifacts)
     - QUEUED -> keep
     - READY -> keep
     - FAILED -> keep (will not be re-executed unless manually retried)
     - BLOCKED -> recompute (dependencies may have changed)
     - CANCELLED -> keep
  3. TaskRunner resumes from step 4 of main loop

IDEMPOTENCY GUARANTEE:
  Before executing any task, check if artifact already exists and is valid.
  If artifact exists and passes verification -> skip execution, mark SUCCEEDED.
  This is ALREADY IMPLEMENTED in pipeline_runner.rs (E_ARTIFACT_MISSING guard).
```

### 11.7 Worker Progress Response (Extended)

```
GET /v1/progress/{job_id}

Response:
{
  "progress": 0.62,
  "stage": "translating",
  "message": "Processing chunk 3/10...",
  "events": [...],
  "tasks": [
    {
      "task_id": "chunk-1-stt",
      "progress": 1.0,
      "stage": "transcribe",
      "status": "succeeded"
    },
    {
      "task_id": "chunk-1-translate",
      "progress": 0.45,
      "stage": "translate",
      "status": "running"
    }
  ]
}

NOTE: This is an EXTENSION of existing endpoint, not a new endpoint.
Task aggregation happens in Rust, not worker.
Worker just passes through per-task progress from CancellationToken.
```

### 11.7a Artifact Fingerprint (NEW)

Before executing any task, check if artifact already exists AND matches current configuration.

FINGERPRINT CALCULATION:
fingerprint = sha256(
task_type + input_hash + params_json + provider + model + model_version
)

Where:
input_hash = sha256 of input artifact content
params_json = task parameters (voice, language, style, etc.)
provider = API provider name
model = model identifier
model_version = model version string

DATABASE:
tasks.result_json stores:
{
"artifact_path": "...",
"fingerprint": "abc123...",
"size": 123456,
"created_at": "..."
}

RESUME/RETRY CHECK:

1. Does artifact file exist? NO -> execute
2. Does result_json have fingerprint? NO -> execute
3. Does current fingerprint match stored fingerprint?
   YES + file valid (size > 0, format correct) -> SKIP, mark SUCCEEDED
   NO -> execute (params changed, stale artifact)

This prevents:

- Re-downloading STT output when resuming
- Using stale translation when user changes model
- Re-rendering when only logo settings changed

NOTE: Artifact verification in pipeline_runner.rs (E_ARTIFACT_MISSING) is a
POST-condition check. Fingerprinting is a PRE-condition check. Both are needed.

### 11.8 Dependency Graph per Mode

```
CLASSIC MODE (dubAudio=true, logoRemoval=true):
  transcribe -> translate -> subtitle -> render
                        |-> tts      -> render
                        |-> logo     -> render

CLASSIC MODE (dubAudio=false, logoRemoval=false):
  transcribe -> translate -> subtitle -> render

CLASSIC MODE (dubAudio=true, logoRemoval=false):
  transcribe -> translate -> subtitle -> render
                        |-> tts      -> render

CHUNKED MODE:
  chunk (single task, internal ThreadPoolExecutor)
```

### 11.9 Concurrency Limits

```
GLOBAL: max_concurrent_tasks = 3

PER-TYPE:
  transcribe: 1  (STT is GPU/CPU heavy)
  translate:  2  (API calls, can parallelize)
  subtitle:   2  (lightweight processing)
  tts:        2  (API calls)
  logo:       1  (image processing)
  render:     1  (ffmpeg, resource heavy)

These limits are CONFIGURABLE in config.
Default values are UX-estimated, not benchmarked.
Agent must audit existing resource usage before setting final values.
```

---

## 12. TEST STRATEGY

### 12.1 Unit Tests

RUST:

- TaskRunner::validate_dag() - cycle detection, missing deps, duplicate IDs
- TaskRunner::check_dependents() - correct state transitions
- TaskRunner::can_spawn() - concurrency limit enforcement
- TaskRunner::run() - happy path, failure propagation, cancellation

FRONTEND:

- buildDependencies() - correct dependency calculation (if kept for testing)
- pipelineProgress() - correct weighted progress
- filteredLogs() - correct task filtering

PYTHON:

- CancellationToken with task_id - correct event routing
- /v1/progress response includes tasks[] array

### 12.2 Integration Tests

END-TO-END:

- Classic pipeline with concurrent stages - all stages complete
- Cancellation during concurrent execution - all tasks cancelled, no orphans
- Retry of failed task - only failed task retries, others continue
- Resume after crash - RUNNING->QUEUED, SUCCEEDED preserved
- Dependency failure - dependents become BLOCKED
- DAG cycle rejection - pipeline fails fast
- Idempotency - re-execution skips existing artifacts

PERFORMANCE:

- Benchmark concurrent vs sequential execution
- Record actual speedup for representative workload
- Verify concurrency limits are respected

### 12.3 Regression Tests

EXISTING TESTS MUST PASS:

- All 28 frontend test files
- All 100+ Rust test files
- All 10 worker test files
- No behavior change for chunked mode

---

## 13. RISK REGISTER

| Risk                                     | Impact | Likelihood | Mitigation                                        |
| ---------------------------------------- | ------ | ---------- | ------------------------------------------------- |
| Database migration fails                 | HIGH   | LOW        | Test migration on empty + populated DB            |
| TaskRunner introduces race conditions    | HIGH   | MEDIUM     | Extensive concurrency testing + atomic DB updates |
| Sync/async boundary issues               | HIGH   | MEDIUM     | Agent must audit tokio runtime before coding      |
| Frontend breaks existing sequential mode | MEDIUM | LOW        | Feature flag for new behavior                     |
| Worker events overload frontend          | MEDIUM | LOW        | Rate limiting on event forwarding                 |
| Progress calculation becomes inaccurate  | LOW    | MEDIUM     | Compare old vs new progress values                |
| Existing tests fail                      | HIGH   | LOW        | Run full test suite before merge                  |
| Cycle in dependency graph                | HIGH   | LOW        | DAG validation rejects invalid graphs             |
| Orphaned tasks after cancel              | HIGH   | LOW        | Atomic status updates + cancel propagation        |
| Resume creates duplicate work            | MEDIUM | LOW        | Artifact verification + idempotency               |

---

## 14. DEPENDENCY GRAPH

```
Phase 0.5 (Task Contract)     MUST FINISH FIRST
         |
         v
Phase 0 (Database)            No behavior change
         |
         v
Phase 1 (Rust TaskRunner)     Core concurrency
         |
    +----+----+
    |         |
    v         v
Phase 2    Phase 3             Can be parallelized
(Frontend)  (Worker)
    |         |
    +----+----+
         |
         v
Phase 4 (Integration Testing) Final validation
```

CRITICAL PATH:

- Phase 0.5 must complete before Phase 0 (contract before code)
- Phase 0 must complete before Phase 1 (DB before TaskRunner)
- Phase 1 must complete before Phase 2 and 3
- Phase 2 and 3 can be parallelized
- Phase 4 requires all previous phases

PARALLEL OPPORTUNITIES:

- Phase 2 (Frontend) and Phase 3 (Worker) can overlap
- Unit tests can be written alongside implementation
- Documentation can be updated during any phase

---

## 15. IMPLEMENTATION ORDER

### Week 0: Contract (NEW)

- Day 1-2: Create docs/TASK_ARCHITECTURE.md (Phase 0.5)
- Day 3: Review and approve contract

### Week 1: Foundation

- Day 1-2: Database migration v9 (Phase 0)
- Day 3-5: Rust TaskRunner core (Phase 1)

### Week 2: Integration

- Day 1-3: Frontend task display (Phase 2)
- Day 4-5: Worker task events (Phase 3)

### Week 3: Testing

- Day 1-2: Unit tests for all new code
- Day 3-4: Integration tests
- Day 5: Performance benchmarking

### Week 4: Polish

- Day 1-2: Bug fixes from testing
- Day 3: Documentation updates
- Day 4: Final review and merge
- Day 5: Deployment and monitoring

---

## 16. ACCEPTANCE CRITERIA (REVISED)

### Functional Requirements

1. **Rust owns dependency graph.** Frontend sends only {project_id, mode, options}. No stage logic in frontend.

2. **Concurrent execution of independent stages.** After translate completes: subtitle, tts, logo start simultaneously. Render waits for all.

3. **Task state machine is correct.** BLOCKED state exists. Failure propagation works. No stuck states.

4. **Progress is accurate.** Weighted progress reflects stage durations. Per-task progress bars work. Overall progress updates smoothly.

5. **Logs are real-time and task-aware.** Task events reach frontend within 1 second. LiveLog can filter by task_id.

6. **Cancellation is complete.** Cancel stops all running tasks. No orphaned tasks. Race conditions handled atomically.

7. **Retry is task-scoped.** Failed task retries independently. Other tasks continue. Dependency failure blocks dependents.

8. **Resume is deterministic.** RUNNING->QUEUED on restart. SUCCEEDED is immutable. Artifact verification prevents duplicate work.

9. **DAG validation rejects invalid graphs.** Cycles detected. Missing dependencies caught. Duplicate task IDs rejected.
10. **Task events include task:blocked.** State machine and event system are consistent.

### Non-Functional Requirements

1. **Performance.** For a representative workload, concurrent execution MUST be faster than sequential execution, with benchmark results recorded.

2. **Reliability.** No data loss during task failures. Clean state after crash and resume. No memory leaks.

3. **Maintainability.** All new code has unit tests. Code follows existing patterns. Documentation updated.

### What We Are NOT Measuring

- Specific speedup percentage (workload-dependent)
- Specific concurrency number (configurable)
- Historical progress estimation (future enhancement)

---

## 17. DEFINITION OF DONE

### Contract Complete

- docs/TASK_ARCHITECTURE.md created and approved
- All 13 architectural fixes incorporated
- No open questions before coding

### Code Complete

- All implementation phases complete
- All acceptance criteria met
- All existing tests pass
- No regressions in functionality

### Quality Complete

- Code review approved
- Unit test coverage > 80% for new code
- Integration tests pass
- Performance benchmarks recorded (not targeting specific number)

### Test Scenarios Must Cover

- DAG: valid graph, missing dependency, cycle, duplicate ID
- Scheduler: global limit, per-type limit, dependency unlock, deadlock detection
- Failure: transient retry, permanent failure, BLOCKED propagation
- Cancellation: queued, ready, running, completion/cancel race
- Recovery: running->queued, succeeded immutable, blocked recomputation, artifact fingerprint
- Concurrency: 3 tasks actually overlap, render waits for all dependencies

### Documentation Complete

- API documentation updated
- Architecture diagram updated
- User guide updated (if UI changes)
- Release notes drafted

### Deployment Complete

- Migration tested on staging
- Feature flag configured
- Monitoring in place
- Rollback plan documented

---

## 18. GLOSSARY

| Term                  | Definition                                                                             |
| --------------------- | -------------------------------------------------------------------------------------- |
| Task                  | A unit of work within a job (e.g., chunk processing, stage execution)                  |
| Dependency            | A task that must complete before another task can start                                |
| Ready Queue           | Tasks that have all dependencies satisfied and can start                               |
| BLOCKED               | Task whose dependency failed; cannot run until dependency retry succeeds               |
| JoinSet               | Rust async task pool for concurrent execution                                          |
| CancellationToken     | Python threading primitive for cooperative cancellation                                |
| TaskRunner            | New Rust component for managing concurrent task execution                              |
| Pipeline Orchestrator | Rust component that owns dependency graph and dispatches to TaskRunner                 |
| Weighted Progress     | Progress calculation that accounts for stage duration differences (UX estimation)      |
| Task Event            | A progress/log message tagged with task_id for frontend display                        |
| DAG                   | Directed Acyclic Graph - dependency structure of tasks                                 |
| Idempotency           | Property where re-executing a completed task produces same result (via artifact check) |

---

## 19. CANCELLATION SEMANTICS (DETAILED)

### Cancellation Propagation Chain

```
User clicks Cancel
  -> Frontend: IPC cancel(project_id)
  -> Rust JobService.cancel(job_id):
     - DB: UPDATE jobs SET cancel_requested=1 WHERE id=? AND status='running'
     - Set in-memory cancel_flag[job_id] = true
  -> TaskRunner checks cancel_token before spawning new tasks
  -> TaskRunner cancels all RUNNING tasks:
     - For each task_handle in running_set:
       - POST /v1/jobs/{job_id}/tasks/{task_id}/cancel (or use existing cancel endpoint)
       -> Worker: token.cancel() -> sets threading.Event
       -> Subprocess monitor: _kill_tree()
  -> DB: UPDATE tasks SET status='cancelled' WHERE job_id=? AND status IN ('queued','ready','running')
  -> All tasks become CANCELLED
  -> Job status -> Cancelled
```

### Race Condition: Completion vs Cancellation

```
SCENARIO: Task A is completing (HTTP 200 arriving) + User presses Cancel

ATOMIC RESOLUTION:
  Task completion:
    UPDATE tasks SET status='succeeded', progress=1.0
    WHERE id=? AND status='running'
    -- Returns: 1 row affected (success) or 0 rows (cancelled won)

  Cancel request:
    UPDATE tasks SET status='cancelled'
    WHERE id=? AND status IN ('queued','ready','running')
    -- Returns: 1 row affected (cancel won) or 0 rows (completion won)

  Only one UPDATE succeeds. The other affects 0 rows.
  Application layer ignores 0-row updates.

RESULT: Consistent state. No UI/DB/Worker mismatch.
```

### Cancel During Different States (V1 = Job-Level, 2-Phase)

```
QUEUED task:   -> CANCELLED (immediate, no worker involved)
READY task:    -> CANCELLED (before spawn, no worker involved)
RUNNING task:  -> Phase 1: signal worker cancel
                 Phase 2: wait for worker confirmation -> CANCELLED
SUCCEEDED:     -> no change (already done, immutable)
FAILED:        -> no change (already failed)
BLOCKED:       -> CANCELLED (dependent cannot run)

V1: Cancel is ALL-OR-NOTHING at job level.
V2: Future - per-task cancel endpoint if needed.
```

IMPORTANT: DB status transition happens AFTER worker confirms termination.
This prevents orphan execution where DB says CANCELLED but process still runs.

---

## 20. RETRY SEMANTICS (DETAILED)

### Task-Level Retry

```
WHEN: Task fails with transient error
  1. Check error classification (transient vs permanent)
  2. If transient AND retry_count < max_retries:
     - retry_count += 1
     - status: FAILED -> QUEUED (re-queued)
     - Exponential backoff: 2s * retry_count
     - Dependents remain BLOCKED/QUEUED (no change)
  3. If permanent OR retry_count >= max_retries:
     - status: FAILED (terminal)
     - Dependents -> BLOCKED
     - Job fails if no other tasks running

ERROR CLASSIFICATION:
  Transient: E_TTS_FAILED, E_API_ERROR, E_API_RATE_LIMIT, network timeout
  Permanent: E_ARTIFACT_MISSING, E_INVALID_INPUT, E_PERMISSION_DENIED

RETRY IS TASK-SCOPED:
  - Does NOT retry entire job
  - Does NOT reset other tasks
  - Does NOT affect already-succeeded tasks
```

### Job-Level Retry (Existing)

```
Job retry = restart entire pipeline from scratch.
All tasks reset to initial state.
Artifact verification prevents re-downloading completed work.
This is SEPARATE from task-level retry.
```

---

## 21. CRASH/RESUME SEMANTICS (DETAILED)

### Restart Algorithm

```
On application restart:
  1. JobService::resume() runs (existing code)
  2. Extended with task recovery:

  for each incomplete job:
    load_tasks(job_id)

    for each task:
      match task.status:
        SUCCEEDED  -> keep (immutable)
        RUNNING    -> task.status = QUEUED (safe to re-execute)
        QUEUED     -> keep
        READY      -> keep
        FAILED     -> keep (not retried automatically)
        BLOCKED    -> recompute from current dependency states
        CANCELLED  -> keep

    TaskRunner resumes from main loop
```

### Artifact Verification on Resume

```
Before executing any task:
  1. Check if output artifact already exists
  2. If exists AND passes verification (non-empty, correct format):
     -> Skip execution, set status = SUCCEEDED
  3. If not exists OR verification fails:
     -> Execute task normally

This is ALREADY IMPLEMENTED in pipeline_runner.rs (E_ARTIFACT_MISSING guard).
TaskRunner leverages existing verification.
```

### Resume Safety Guarantees

```
- SUCCEEDED tasks are NEVER re-executed (idempotency)
- RUNNING tasks are safely re-queued (cancellation was not graceful)
- BLOCKED tasks are recomputed (dependency states may have changed)
- No duplicate artifacts produced
- No data corruption from partial writes
```

---

## 22. IDEMPOTENCY/ARTIFACT SEMANTICS

### Artifact Verification Points

```
EVERY stage result goes through:
  1. Worker validates its own output before HTTP 200
  2. Rust runner verifies artifact file exists AND is non-empty:
     - audio: pipeline_runner.rs:533-542
     - subtitle output: pipeline_runner.rs:1114-1120
     - voice track: pipeline_runner.rs:937-948
     - rendered video: pipeline_runner.rs:1255-1263
     - logo removal: pipeline_runner.rs:1009-1017
     - audio processing: pipeline_runner.rs:1062-1070
  3. If file missing or empty: E_ARTIFACT_MISSING (Permanent error)
```

### Skip Logic

```
Before executing task T:
  1. Get expected output path from params_json
  2. Check if output file exists
  3. If exists:
     a. Check file size > 0
     b. Check file format (if applicable)
     c. If all checks pass -> skip execution
     d. If any check fails -> re-execute
  4. If not exists -> execute normally
```

---

## 23. SYNC/ASYNC MODEL DECISION

### Current Runtime Model

```
Tauri main.rs:
  - Uses tokio runtime (Tauri requirement)
  - JobService runs on tokio::spawn (async)
  - worker_loop is async
  - PipelineRunner is async
  - HTTP polling uses reqwest (async)

Key: The codebase IS already async. JoinSet is appropriate.
```

### Decision

```
USE: tokio::task::JoinSet for TaskRunner

WHY:
  - Codebase already uses tokio runtime
  - PipelineRunner is already async
  - JoinSet integrates naturally with existing async code
  - No sync/async boundary issues

AGENT MUST VERIFY:
  - Tauri runtime configuration in main.rs
  - Whether multi-threaded or current-thread runtime
  - Impact of JoinSet on existing async code
  - Test concurrent task execution under load
```

---

## 24. TASK API/EVENT CONTRACT

### Events from Rust to Frontend

```
All events share this envelope:
{
  "event_id": "uuid",
  "sequence": 12345,
  "timestamp": "ISO8601",
  "job_id": "uuid",
  "task_id": "uuid"
}

task:created
  {envelope, task_type, stage, depends_on}

task:started
  {envelope, task_type, stage}

task:progress
  {envelope, progress, stage, message}
  Rate limit: max 10 events/sec/task OR progress delta >= 1%

task:succeeded
  {envelope, task_type, stage, result_json}

task:failed
  {envelope, task_type, stage, error_code, error_message}

task:cancelled
  {envelope, task_type, stage}

task:blocked
  {envelope, task_type, stage, blocked_by: [...task_ids]}

SEQUENCE NUMBER:
  Frontend uses sequence to handle out-of-order delivery:
    if (event.sequence <= lastSequence[event.task_id]) ignore
  Prevents progress regression (70% -> 60%) from event race.
```

### IPC from Frontend to Rust

```
AUTOMATE
  {project_id, mode: "classic"|"chunked", options: {dubAudio, logoRemoval, ...}}

CANCEL
  {project_id}

GET_TASKS
  {project_id} -> [{task_id, task_type, stage, status, progress, depends_on}]
```

### Existing Events (Unchanged)

```
job:status  (extended with task_count, tasks_succeeded, tasks_failed)
job:log     (extended with task_id field)
```

---

## 25. V1 MVP SCOPE

### INCLUDE in V1

```
- tasks table with full schema
- Rust TaskRunner with dependency DAG
- DAG validation (cycle detection, missing deps)
- Concurrency = 3 (global limit)
- Per-type concurrency limits (configurable)
- Task state machine (all 7 states)
- Failure propagation (FAILED -> BLOCKED)
- Task-level retry (max 3, exponential backoff)
- Cancellation propagation (top-down)
- Race condition handling (atomic DB updates)
- Crash/resume (RUNNING->QUEUED, artifact verification)
- Frontend: task list display with status/progress
- Frontend: task-based log filtering
- Weighted progress (configurable weights)
- Worker: extended /v1/progress with tasks[] array
```

### EXCLUDE from V1 (Future Enhancements)

```
- Fancy LiveLog filtering UI
- Historical progress estimation
- Per-task cancel button (V1 = job-level cancel only)
- Complex performance optimization
- Task dependency visualization in UI
- Manual task retry button
- Task priority queue
- Dynamic concurrency adjustment
```

### V1 Success Criteria

```
- Classic pipeline with concurrent subtitle+tts+logo
- All existing tests pass
- No regressions in chunked mode
- Benchmark results recorded
- docs/TASK_ARCHITECTURE.md as single source of truth
```
