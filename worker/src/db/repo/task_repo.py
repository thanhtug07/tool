"""Task repository for CRUD operations on the tasks table."""

import sqlite3
from typing import Optional, List
from src.db.models import Task, TaskType, TaskStatus


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        job_id=row["job_id"],
        task_type=TaskType(row["task_type"]),
        stage=row["stage"],
        status=TaskStatus(row["status"]),
        progress=float(row["progress"]),
        depends_on=row["depends_on"],
        params_json=row["params_json"],
        input_fingerprint=row["input_fingerprint"],
        result_json=row["result_json"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        retry_count=int(row["retry_count"]),
        max_attempts=int(row["max_attempts"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class TaskRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_task(self, task: Task) -> None:
        self.conn.execute(
            """
            INSERT INTO tasks (
                id, job_id, task_type, stage, status, progress,
                depends_on, params_json, input_fingerprint, result_json,
                error_code, error_message, retry_count, max_attempts,
                cancel_requested, created_at, updated_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.job_id,
                task.task_type.value if isinstance(task.task_type, TaskType) else task.task_type,
                task.stage,
                task.status.value if isinstance(task.status, TaskStatus) else task.status,
                task.progress,
                task.depends_on,
                task.params_json,
                task.input_fingerprint,
                task.result_json,
                task.error_code,
                task.error_message,
                task.retry_count,
                task.max_attempts,
                1 if task.cancel_requested else 0,
                task.created_at,
                task.updated_at,
                task.started_at,
                task.finished_at,
            ),
        )

    def get_task(self, task_id: str) -> Optional[Task]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, job_id, task_type, stage, status, progress,
                   depends_on, params_json, input_fingerprint, result_json,
                   error_code, error_message, retry_count, max_attempts,
                   cancel_requested, created_at, updated_at, started_at, finished_at
            FROM tasks WHERE id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_task(row)

    def get_tasks_by_job(self, job_id: str) -> List[Task]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, job_id, task_type, stage, status, progress,
                   depends_on, params_json, input_fingerprint, result_json,
                   error_code, error_message, retry_count, max_attempts,
                   cancel_requested, created_at, updated_at, started_at, finished_at
            FROM tasks WHERE job_id = ? ORDER BY created_at
            """,
            (job_id,),
        )
        return [_row_to_task(row) for row in cursor.fetchall()]

    def update_task_status(self, task_id: str, new_status: TaskStatus, now: str) -> bool:
        current = self.get_task(task_id)
        if not current:
            return False

        if not current.status.can_transition(new_status):
            return False

        finished_at = now if new_status.is_terminal() else None
        started_at = now if (new_status == TaskStatus.RUNNING and not current.started_at) else current.started_at

        cursor = self.conn.execute(
            """
            UPDATE tasks SET status = ?, updated_at = ?, finished_at = ?, started_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                new_status.value,
                now,
                finished_at,
                started_at,
                task_id,
                current.status.value,
            ),
        )
        return cursor.rowcount > 0

    def update_task_progress(self, task_id: str, progress: float, now: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE tasks SET progress = ?, updated_at = ? WHERE id = ?",
            (progress, now, task_id),
        )
        return cursor.rowcount > 0

    def update_task_result(self, task_id: str, result_json: str, now: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE tasks SET result_json = ?, updated_at = ? WHERE id = ?",
            (result_json, now, task_id),
        )
        return cursor.rowcount > 0

    def increment_retry(self, task_id: str, error_code: str, error_message: str, now: str) -> bool:
        cursor = self.conn.execute(
            """
            UPDATE tasks SET retry_count = retry_count + 1, error_code = ?,
                             error_message = ?, updated_at = ? WHERE id = ?
            """,
            (error_code, error_message, now, task_id),
        )
        return cursor.rowcount > 0

    def cancel_all_non_succeeded(self, job_id: str, now: str) -> int:
        cursor = self.conn.execute(
            """
            UPDATE tasks SET status = 'cancelled', cancel_requested = 1,
                             finished_at = ?, updated_at = ?
            WHERE job_id = ? AND status NOT IN ('succeeded', 'cancelled')
            """,
            (now, now, job_id),
        )
        return cursor.rowcount

    def resume_running_tasks(self, job_id: str, now: str) -> int:
        cursor = self.conn.execute(
            """
            UPDATE tasks SET status = 'queued', updated_at = ?
            WHERE job_id = ? AND status = 'running'
            """,
            (now, job_id),
        )
        return cursor.rowcount

    def all_tasks_terminal(self, job_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM tasks WHERE job_id = ? AND status NOT IN
            ('succeeded', 'failed', 'cancelled')
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        return row[0] == 0 if row else True

    def any_task_failed(self, job_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE job_id = ? AND status = 'failed'", (job_id,))
        row = cursor.fetchone()
        return row[0] > 0 if row else False
