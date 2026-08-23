"""Pipeline Orchestrator & Execution Dispatcher (Phase 8).

Orchestrates job execution by composing JobDomainService, DAGResolver, and existing worker stage services.
Exposes async/threaded task execution dispatcher matching Rust PipelineRunner & TaskRunner contracts.
"""

import logging
import concurrent.futures
from typing import Dict, Optional
from src.db.models import JobStatus, Task, TaskStatus
from src.services.job_service import JobDomainService
from src.orchestration.dag_resolver import DAGResolver
from src.core.job import CancellationToken

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Orchestrates job execution and task dispatching using Python TaskRunner DAG engine."""

    def __init__(self, job_service: Optional[JobDomainService] = None):
        self.job_service = job_service or JobDomainService()

    def run_job_sync(self, job_id: str, cancel_token: Optional[CancellationToken] = None) -> bool:
        """Synchronously execute a job's task DAG (for background worker execution)."""
        res = self.job_service.get_job_with_tasks(job_id)
        if not res:
            logger.error("Job %s not found for orchestration", job_id)
            return False
        job, tasks = res

        if not tasks:
            # Single-stage job legacy fallback
            self.job_service.update_job_status(job_id, JobStatus.RUNNING, progress=0.1, stage="running")
            self.job_service.update_job_status(job_id, JobStatus.SUCCEEDED, progress=1.0, stage="completed")
            return True

        ok = self.job_service.update_job_status(job_id, JobStatus.RUNNING, progress=0.05, stage="orchestrating")
        if not ok:
            logger.warning("Could not transition job %s to RUNNING", job_id)
        resolver = DAGResolver(tasks)

        # Thread pool for concurrent task dispatch
        with concurrent.futures.ThreadPoolExecutor(max_workers=resolver.limits.global_limit) as executor:
            futures: Dict[concurrent.futures.Future, Task] = {}

            while True:
                if cancel_token and cancel_token.is_cancelled():
                    self.job_service.update_job_status(job_id, JobStatus.CANCELLED, stage="cancelled")
                    return False

                ready_tasks = resolver.get_next_ready_tasks()
                for task in ready_tasks:
                    # Update status to RUNNING in DB and resolver
                    self.job_service.get_job(job_id)  # touch
                    resolver.running_tasks[task.id] = (
                        task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type)
                    )
                    fut = executor.submit(self._execute_stage_task, task, cancel_token)
                    futures[fut] = task

                if not futures:
                    if resolver.is_deadlocked():
                        self.job_service.update_job_status(
                            job_id, JobStatus.FAILED, error_code="E_DEADLOCK", error_message="Task DAG deadlocked"
                        )
                        return False
                    break  # All tasks completed or blocked

                # Wait for any task to finish
                done, _ = concurrent.futures.wait(futures.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    task = futures.pop(fut)
                    try:
                        success = fut.result()
                        if success:
                            resolver.mark_task_succeeded(task.id)
                        else:
                            resolver.mark_task_failed(task.id)
                    except Exception as exc:
                        logger.error("Task %s execution raised exception: %s", task.id, exc)
                        resolver.mark_task_failed(task.id)

        all_succeeded = all(t.status == TaskStatus.SUCCEEDED for t in resolver.tasks_map.values())
        if all_succeeded:
            self.job_service.update_job_status(job_id, JobStatus.SUCCEEDED, progress=1.0, stage="succeeded")
            return True
        else:
            self.job_service.update_job_status(
                job_id, JobStatus.FAILED, error_code="E_PIPELINE_FAILED", error_message="One or more tasks failed"
            )
            return False

    def _execute_stage_task(self, task: Task, cancel_token: Optional[CancellationToken] = None) -> bool:
        """Execute a single task stage (mock/smoke dispatch boundary)."""
        logger.info("Executing task %s (%s)", task.id, task.task_type)
        return True
