"""Job domain service for job lifecycle, state transitions, queueing, and task management."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import queue
import threading

from src.db.connection import Database
from src.db.models import Job, JobType, JobStatus, Task, TaskType, TaskStatus, ProjectStatus
from src.db.repo import JobRepo, TaskRepo, ProjectRepo, SettingsRepo
from src.core.job import get_cancel_token, CancellationToken

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class JobDomainService:
    """Manages job submission, retrieval, state transitions, task generation, and FIFO queueing."""

    def __init__(self, db: Database):
        self.db = db
        self.job_queue: queue.Queue[str] = queue.Queue()
        self._cancel_tokens: Dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def get_token(self, job_id: str) -> CancellationToken:
        with self._lock:
            if job_id not in self._cancel_tokens:
                self._cancel_tokens[job_id] = get_cancel_token(job_id)
            return self._cancel_tokens[job_id]

    def remove_token(self, job_id: str) -> None:
        with self._lock:
            self._cancel_tokens.pop(job_id, None)

    def submit(
        self,
        project_id: str,
        job_type: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Job:
        params = params or {}
        now = utc_now_iso()

        with self.db.transaction() as conn:
            p_repo = ProjectRepo(conn)
            project = p_repo.get(project_id)
            if not project:
                raise ValueError(f"Project not found: {project_id}")

            j_repo = JobRepo(conn)
            job_id = j_repo.next_id()

            # Parse job type
            try:
                j_type_enum = JobType(job_type)
            except ValueError:
                j_type_enum = JobType.TRANSCRIBE

            job = Job(
                id=job_id,
                project_id=project_id,
                job_type=j_type_enum,
                status=JobStatus.QUEUED,
                progress=0.0,
                stage="queued",
                created_at=now,
                updated_at=now,
                params=params,
            )
            j_repo.insert(job)

            # Check if orchestrator_v2 is enabled
            s_repo = SettingsRepo(conn)
            v2_enabled = s_repo.get("automation.orchestrator_v2")

            if v2_enabled:
                t_repo = TaskRepo(conn)
                # Create initial task row for orchestration
                task = Task(
                    id=f"{job_id}_task_1",
                    job_id=job_id,
                    task_type=TaskType(j_type_enum.value),
                    stage=j_type_enum.value,
                    status=TaskStatus.QUEUED,
                    progress=0.0,
                    depends_on="[]",
                    created_at=now,
                    updated_at=now,
                    params_json=json.dumps(params),
                )
                t_repo.create_task(task)

        # Append event to token
        token = self.get_token(job_id)
        token.set_event("info", f"Job {job_id} submitted and queued.")

        # Enqueue to FIFO queue
        self.job_queue.put(job_id)

        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        conn = self.db.conn()
        j_repo = JobRepo(conn)
        return j_repo.get(job_id)

    def list_jobs(self, limit: int = 50) -> List[Job]:
        conn = self.db.conn()
        j_repo = JobRepo(conn)
        return j_repo.list_recent(limit)

    def list_jobs_by_project(self, project_id: str) -> List[Job]:
        conn = self.db.conn()
        j_repo = JobRepo(conn)
        return j_repo.list_by_project(project_id)

    def list_tasks(self, job_id: str) -> List[Task]:
        conn = self.db.conn()
        t_repo = TaskRepo(conn)
        return t_repo.get_tasks_by_job(job_id)

    def cancel(self, job_id: str) -> Job:
        now = utc_now_iso()
        with self.db.transaction() as conn:
            j_repo = JobRepo(conn)
            job = j_repo.get(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")

            if job.status.is_terminal():
                return job

            if job.status == JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED
                job.stage = "cancelled"
                job.finished_at = now
                job.updated_at = now
                j_repo.update(job)

                t_repo = TaskRepo(conn)
                t_repo.cancel_all_non_succeeded(job_id, now)
            else:
                # RUNNING job: set cancel_requested
                job.cancel_requested = True
                job.updated_at = now
                j_repo.update(job)

                t_repo = TaskRepo(conn)
                t_repo.cancel_all_non_succeeded(job_id, now)

        # Trigger in-memory token cancel
        token = self.get_token(job_id)
        token.cancel()
        token.set_event("warning", f"Job {job_id} cancellation requested.")

        return self.get_job(job_id)  # type: ignore

    def retry(self, job_id: str) -> Job:
        now = utc_now_iso()
        with self.db.transaction() as conn:
            j_repo = JobRepo(conn)
            job = j_repo.get(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")

            if not (job.status.is_terminal() and job.status != JobStatus.SUCCEEDED):
                raise ValueError(f"Job {job_id} in status {job.status.value} cannot be retried")

            job.status = JobStatus.QUEUED
            job.progress = 0.0
            job.stage = "queued"
            job.error_code = None
            job.error_message = None
            job.error_log = None
            job.cancel_requested = False
            job.retry_count += 1
            job.started_at = None
            job.finished_at = None
            job.updated_at = now
            j_repo.update(job)

            # Reset task statuses
            t_repo = TaskRepo(conn)
            tasks = t_repo.get_tasks_by_job(job_id)
            for t in tasks:
                t_repo.update_task_status(t.id, TaskStatus.QUEUED, now)
                t_repo.update_task_progress(t.id, 0.0, now)

        # Clear cancellation token
        token = self.get_token(job_id)
        token.reset()
        token.set_event("info", f"Job {job_id} retried and re-queued.")

        # Re-enqueue
        self.job_queue.put(job_id)

        return self.get_job(job_id)  # type: ignore

    def crash_resume(self) -> None:
        """On backend startup, re-seed queued jobs and handle interrupted running jobs."""
        now = utc_now_iso()
        with self.db.transaction() as conn:
            j_repo = JobRepo(conn)
            t_repo = TaskRepo(conn)

            # Handle running jobs
            running_jobs = j_repo.list_running()
            for job in running_jobs:
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                    job.stage = "cancelled"
                    job.finished_at = now
                    job.updated_at = now
                    j_repo.update(job)
                    t_repo.cancel_all_non_succeeded(job.id, now)
                else:
                    # Transition back to QUEUED
                    job.status = JobStatus.QUEUED
                    job.stage = "queued"
                    job.updated_at = now
                    j_repo.update(job)
                    t_repo.resume_running_tasks(job.id, now)
                    self.job_queue.put(job.id)

            # Seed existing queued jobs
            queued_jobs = j_repo.list_queued()
            for job in queued_jobs:
                self.job_queue.put(job.id)

        logger.info(f"Crash resume completed. Queued jobs count: {self.job_queue.qsize()}")
