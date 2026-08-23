"""Job repository for CRUD operations on the jobs table."""

import json
import sqlite3
from typing import Optional, List, Dict, Any
from src.db.models import Job, JobType, JobStatus


def _row_to_job(row: sqlite3.Row) -> Job:
    params_raw = row["params_json"]
    try:
        params = json.loads(params_raw) if params_raw else {}
    except Exception:
        params = {}

    return Job(
        id=row["id"],
        project_id=row["project_id"],
        job_type=JobType(row["type"]),
        status=JobStatus(row["status"]),
        progress=float(row["progress"]),
        stage=row["stage"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_log=row["error_log"],
        params=params,
        retry_count=int(row["retry_count"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class JobRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, job: Job) -> None:
        params_json = json.dumps(job.params) if isinstance(job.params, dict) else "{}"
        self.conn.execute(
            """
            INSERT INTO jobs (id, project_id, type, status, progress, stage, error_code,
                               error_message, error_log, params_json, retry_count,
                               cancel_requested, created_at, updated_at, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.project_id,
                job.job_type.value if isinstance(job.job_type, JobType) else job.job_type,
                job.status.value if isinstance(job.status, JobStatus) else job.status,
                job.progress,
                job.stage,
                job.error_code,
                job.error_message,
                job.error_log,
                params_json,
                job.retry_count,
                1 if job.cancel_requested else 0,
                job.created_at,
                job.updated_at,
                job.started_at,
                job.finished_at,
            ),
        )

    def get(self, job_id: str) -> Optional[Job]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, type, status, progress, stage, error_code,
                   error_message, error_log, params_json, retry_count, cancel_requested,
                   created_at, updated_at, started_at, finished_at
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_job(row)

    def list_by_project(self, project_id: str) -> List[Job]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, type, status, progress, stage, error_code,
                   error_message, error_log, params_json, retry_count, cancel_requested,
                   created_at, updated_at, started_at, finished_at
            FROM jobs WHERE project_id = ? ORDER BY updated_at DESC
            """,
            (project_id,),
        )
        return [_row_to_job(row) for row in cursor.fetchall()]

    def list_recent(self, limit: int = 50) -> List[Job]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, type, status, progress, stage, error_code,
                   error_message, error_log, params_json, retry_count, cancel_requested,
                   created_at, updated_at, started_at, finished_at
            FROM jobs ORDER BY updated_at DESC LIMIT ?
            """,
            (max(1, limit),),
        )
        return [_row_to_job(row) for row in cursor.fetchall()]

    def list_queued(self) -> List[Job]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, type, status, progress, stage, error_code,
                   error_message, error_log, params_json, retry_count, cancel_requested,
                   created_at, updated_at, started_at, finished_at
            FROM jobs WHERE status = 'queued' ORDER BY created_at ASC
            """
        )
        return [_row_to_job(row) for row in cursor.fetchall()]

    def list_running(self) -> List[Job]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, type, status, progress, stage, error_code,
                   error_message, error_log, params_json, retry_count, cancel_requested,
                   created_at, updated_at, started_at, finished_at
            FROM jobs WHERE status = 'running' ORDER BY created_at ASC
            """
        )
        return [_row_to_job(row) for row in cursor.fetchall()]

    def update(self, job: Job) -> bool:
        params_json = json.dumps(job.params) if isinstance(job.params, dict) else "{}"
        cursor = self.conn.execute(
            """
            UPDATE jobs
            SET project_id = ?, type = ?, status = ?, progress = ?, stage = ?,
                error_code = ?, error_message = ?, error_log = ?, params_json = ?,
                retry_count = ?, cancel_requested = ?, updated_at = ?,
                started_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                job.project_id,
                job.job_type.value if isinstance(job.job_type, JobType) else job.job_type,
                job.status.value if isinstance(job.status, JobStatus) else job.status,
                job.progress,
                job.stage,
                job.error_code,
                job.error_message,
                job.error_log,
                params_json,
                job.retry_count,
                1 if job.cancel_requested else 0,
                job.updated_at,
                job.started_at,
                job.finished_at,
                job.id,
            ),
        )
        return cursor.rowcount > 0

    def next_id(self) -> str:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(CAST(substr(id, 5) AS INTEGER)), 0) + 1 FROM jobs")
        row = cursor.fetchone()
        n = row[0] if row else 1
        return f"job_{n:04d}"
