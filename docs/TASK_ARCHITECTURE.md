# TASK_ARCHITECTURE.md - Task Orchestration Contract

> Version: 1.0 | Date: 2026-08-22
> Status: LOCKED - single source of truth for task orchestration semantics.

---

## 1. Overview

Rust owns the orchestration. Frontend is a dumb renderer.

---

## 2. Task State Machine

### 2.1 States

| State | Meaning |
|-------|---------|
| QUEUED | Not ready: deps not satisfied or retry backoff |
| READY | Deps satisfied, waiting for concurrency slot |
| RUNNING | Actively executing |
| SUCCEEDED | Done, immutable |
| FAILED | Permanent failure (terminal) |
| CANCELLED | Cancelled (terminal) |
| BLOCKED | Dependency failed permanently |

### 2.2 State Transitions

`
QUEUED -> READY (deps satisfied)
QUEUED -> BLOCKED (dep permanently FAILED)
READY -> RUNNING (slot available)
RUNNING -> SUCCEEDED
RUNNING -> QUEUED (transient fail, retry available)
RUNNING -> FAILED (permanent fail or retry exhausted)
RUNNING -> CANCELLED (confirmed by worker)
BLOCKED -> QUEUED (dep retry succeeds - V2)
`

### 2.3 Terminal States

SUCCEEDED, FAILED, CANCELLED are terminal.
BLOCKED is semi-terminal (unblocked only if dep retry succeeds in V2).

### 2.4 State Invariants

| State | Invariants |
|-------|-----------|
| SUCCEEDED | progress=1.0, finished_at!=null, result_json!=null |
| RUNNING | started_at!=null, finished_at==null |
| FAILED | error_code!=null, error_message!=null, finished_at!=null |
| CANCELLED | cancel_requested=1, finished_at!=null |
| BLOCKED | dependency with status=FAILED |
| QUEUED(retry) | retry_count>0, retains error info |

---

## 3. Dependency DAG

### 3.1 DAG Structure

CLASSIC (dubAudio=true, logoRemoval=true):
  transcribe -> translate -> subtitle -> render
                        |-> tts      -> render
                        |-> logo?    -> render (pending audit)

CLASSIC (dubAudio=false, logoRemoval=false):
  transcribe -> translate -> subtitle -> render

CHUNKED: chunk (single task, ThreadPoolExecutor - unchanged)

### 3.2 DAG Rules

1. Dependencies are DATA dependencies, not UX ordering
2. Render depends on ALL enabled tasks (dynamic generation)
3. DAG built by Rust PipelineOrchestrator, not frontend
4. DAG validated before execution (cycle detection, existence check)

### 3.3 DAG Validation

Before TaskRunner starts:
1. All depends_on references exist as task IDs
2. No cycles (Kahn topological sort)
3. No duplicate task IDs
4. All task types recognized

---

## 4. Concurrency

### 4.1 Model

INTER-JOB: 1 (JobService FIFO unchanged)
INTRA-JOB: N (TaskRunner with limits)

### 4.2 Limits

ConcurrencyConfig: global=3, per_type={transcribe:1,translate:2,subtitle:2,tts:2,logo:1,render:1}

DEFAULT/EXPERIMENTAL. Configurable, not hard-coded.

### 4.3 Scheduler Rules

1. Scan ready queue, spawn all that can spawn
2. Skip unavailable tasks, put in deferred queue
3. Do NOT break on first unavailable (no head-of-line blocking)
4. Enforce global AND per-type limits
5. Deterministic FIFO within groups

---

## 5. Cancellation

### 5.1 V1 Scope

Job-level cancellation only. No per-task cancel endpoint.

### 5.2 Two-Phase Cancellation

Phase 1: REQUEST
- User clicks Cancel
- Frontend sends CANCEL(project_id) via IPC
- Rust JobService: DB SET cancel_requested=1, in-memory flag=true
- TaskRunner: stop spawning, POST /v1/jobs/{id}/cancel to worker
- Do NOT set task status to CANCELLED yet

Phase 2: CONFIRM
- Worker kills subprocesses, responds (or exits)
- Rust: UPDATE tasks SET status=cancelled WHERE id=? AND status=running
- Only tasks still running in DB get cancelled
- Tasks completed between Phase 1-2 are already succeeded

### 5.3 Why Two-Phase

DB status=running does NOT mean process is alive.
Must wait for worker confirmation to prevent orphan execution.

### 5.4 Race Condition

Task completing + Cancel pressed:
- Completion: UPDATE ... SET status=succeeded WHERE id=? AND status=running
- Cancel: UPDATE ... SET status=cancelled WHERE id=? AND status=running
- Only one succeeds. Loser affects 0 rows.

---

## 6. Retry

### 6.1 Task-Level Retry Only

V1: job retry DISABLED (explicit user action only).

### 6.2 Semantics

When task T fails:
1. Classify: transient or permanent
2. If transient AND retry_count < (max_attempts - 1):
   - retry_count += 1
   - status -> QUEUED
   - Backoff: 2s * retry_count
   - Dependents: NO CHANGE
3. If permanent OR retry_count >= (max_attempts - 1):
   - status -> FAILED (terminal)
   - Dependents -> BLOCKED

### 6.3 Terminology

max_attempts=3: attempt 1 + 2 retries = 3 total executions
retry_count starts at 0, increments on each retry

### 6.4 Key Rule

BLOCKED ONLY created when dependency reaches FAILED (terminal).
Transient failure + retry does NOT create BLOCKED.

### 6.5 Error Classification

| Error | Type |
|-------|------|
| E_TTS_FAILED | Transient |
| E_API_ERROR | Transient |
| E_API_RATE_LIMIT | Transient |
| Network timeout | Transient |
| E_ARTIFACT_MISSING | Permanent |
| E_INVALID_INPUT | Permanent |
| E_PERMISSION_DENIED | Permanent |

---

## 7. Crash/Resume

### 7.1 Resume Algorithm

On restart:
1. Load tasks for incomplete jobs
2. Apply transitions:
   - SUCCEEDED -> keep (immutable)
   - RUNNING -> QUEUED (safe to re-execute)
   - QUEUED -> keep
   - READY -> keep
   - FAILED -> keep
   - BLOCKED -> recompute from current deps
   - CANCELLED -> keep
3. TaskRunner resumes from main loop

### 7.2 Idempotency via Artifacts

Before executing any task:
1. Check if output artifact exists
2. If exists, check result_json fingerprint
3. Fingerprint matches + file valid -> SKIP, mark SUCCEEDED
4. Otherwise -> execute normally

---

## 8. Artifact Fingerprint

### 8.1 Calculation

fingerprint = sha256(task_type + input_hash + params_json + provider + model + model_version)

### 8.2 Storage (in result_json)

{artifact_path, fingerprint, size, sha256, created_at}

### 8.3 Check

1. Artifact exists? NO -> execute
2. Fingerprint in result_json? NO -> execute
3. Fingerprint matches + valid? -> SKIP
4. Otherwise -> execute

### 8.4 Distinction

E_ARTIFACT_MISSING = POST-condition (after execution)
Fingerprinting = PRE-condition (before execution)
Both needed.

---

## 9. Job State Aggregation

| Job State | Condition |
|-----------|-----------|
| QUEUED | Tasks created, TaskRunner not started |
| RUNNING | At least one task RUNNING/READY/QUEUED |
| SUCCEEDED | ALL tasks SUCCEEDED |
| FAILED | ANY task FAILED terminal AND no retry pending |
| CANCELLED | Cancel requested AND all non-terminal cancelled |

Job does NOT have BLOCKED state. BLOCKED is task-only.

---

## 10. Event Contract

### 10.1 Envelope

{event_id, sequence, timestamp, job_id, task_id}

### 10.2 Events

task:created    {envelope, task_type, stage, depends_on}
task:started    {envelope, task_type, stage}
task:progress   {envelope, progress, stage, message}
task:succeeded  {envelope, task_type, stage, result_json}
task:failed     {envelope, task_type, stage, error_code, error_message}
task:cancelled  {envelope, task_type, stage}
task:blocked    {envelope, task_type, stage, blocked_by}

### 10.3 Rate Limiting

progress: max 10 events/sec/task OR delta >= 1%
Terminal events: always forward

### 10.4 Sequence

Frontend ignores events with sequence <= last seen per task.

---

## 11. Feature Flag

automation_orchestrator_v2 = false (default OFF)
OFF: current pipeline. ON: TaskRunner V2.
Rollback: flip OFF, no DB migration needed.

---

## 12. Database Schema

tasks table (migration v9):
id TEXT PK, job_id TEXT FK, task_type TEXT, stage TEXT,
status TEXT DEFAULT queued, progress REAL DEFAULT 0,
depends_on TEXT DEFAULT [], params_json TEXT,
input_fingerprint TEXT, result_json TEXT,
error_code TEXT, error_message TEXT,
retry_count INT DEFAULT 0, max_attempts INT DEFAULT 3,
cancel_requested INT DEFAULT 0,
created_at TEXT, updated_at TEXT, started_at TEXT, finished_at TEXT

---

## 13. V1 Scope

INCLUDE: task table, DAG, concurrency=3, 7 states, task retry,
2-phase cancel, crash resume, artifact fingerprint, task events,
weighted progress, feature flag, baseline benchmark

EXCLUDE: per-task cancel, priority queue, dynamic concurrency,
manual retry UI, task visualization, historical progress

## 14. V2 Debts (Phase 3 → Phase 4)

- `fingerprint` after success now writes `result_json.fingerprint` via `task_runner.rs:444` (input_fingerprint → result_json) so second run hits cache; worker still not writing fingerprint itself.
- `StudioWorkspace.tsx:859` still `setPlan(steps)` for v2 but Rust DAG from `pipeline.submit` now matches `steps` (classic + custom linear, `job_service.rs:369`), so checklist not lệch. Full dumb renderer (derive checklist from `task.list`) deferred to Phase 4.
- `LiveLog` still `job:log` only; `task:log` with `taskId` filter deferred (poll `task.list` + `pipelineProgressFromTasks` covers progress).
- Worker `tasks[]` prefix `f"{job_id}:"` (`worker/src/api/pipeline.py:1220`) can collide `job_1` vs `job_10`; fix to store `pipeline_job_id` column or check `tid == f"{job_id}:" + suffix` exact.
- `TaskEventSink` push vs poll: v2 currently poll `task.list` 500ms (`StudioWorkspace.tsx:337`) not Tauri `task:status` push; Phase 3 will wire `AppTaskSink` (`lib.rs:38`) to emit `task:status/progress` via `RateLimitedSink` (`task_runner.rs:95`).

