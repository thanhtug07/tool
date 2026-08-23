"""Web API foundation routes for pure Vite localhost mode (Phase 3).

Exposes minimal, read-only foundation endpoints:
- ``GET /api/health`` — public/cheap health check (no bearer required, for browser dev / proxy)
- ``GET /api/system/status`` — hardware/system status
- ``GET /api/projects`` — project list (reads SQLite v9 `projects` table)
"""

import logging
import os
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src import __version__
from src.db.connection import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["web-foundation"])


class WebHealthResponse(BaseModel):
    status: str = "ok"
    version: str
    backend: str = "fastapi-localhost"


class SystemStatusResponse(BaseModel):
    version: str
    status: str = "running"
    db_version: int


class ProjectModel(BaseModel):
    id: str
    name: str
    source_video_path: str
    status: str
    created_at: str
    updated_at: str


@router.get("/health", response_model=WebHealthResponse)
def web_health() -> WebHealthResponse:
    """Public health endpoint for web frontend / dev proxy health checks."""
    return WebHealthResponse(status="ok", version=__version__)


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status() -> SystemStatusResponse:
    """Report backend system status and SQLite schema version."""
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA user_version;")
            row = cur.fetchone()
            db_ver = row[0] if row else 0
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to query DB user_version: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database query failed",
        ) from exc

    return SystemStatusResponse(
        version=__version__,
        status="running",
        db_version=db_ver,
    )


class JobModel(BaseModel):
    id: str
    project_id: str
    job_type: str
    status: str
    progress: float
    stage: str
    created_at: str
    updated_at: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TaskModel(BaseModel):
    id: str
    job_id: str
    task_type: str
    stage: str
    status: str
    progress: float
    depends_on: str
    created_at: str
    updated_at: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobWithTasksModel(JobModel):
    tasks: List[TaskModel] = Field(default_factory=list)


@router.get("/projects", response_model=List[ProjectModel])
def list_projects() -> List[ProjectModel]:
    """List projects from SQLite database using ProjectRepo."""
    from src.db.repo.project_repo import ProjectRepo  # noqa: PLC0415
    try:
        conn = get_connection()
        try:
            repo = ProjectRepo(conn)
            projects = repo.list()
            return [
                ProjectModel(
                    id=p.id,
                    name=p.name,
                    source_video_path=p.source_video_path,
                    status=p.status.value if hasattr(p.status, "value") else str(p.status),
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                )
                for p in projects
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to fetch projects: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list projects",
        ) from exc


@router.get("/jobs", response_model=List[JobModel])
def list_jobs(project_id: Optional[str] = None, limit: int = 50) -> List[JobModel]:
    """List jobs (filtered by project_id or recent dashboard feed)."""
    from src.db.repo.job_repo import JobRepo  # noqa: PLC0415
    try:
        conn = get_connection()
        try:
            repo = JobRepo(conn)
            if project_id:
                jobs = repo.list_by_project(project_id)
            else:
                jobs = repo.list_recent(limit)
            return [
                JobModel(
                    id=j.id,
                    project_id=j.project_id,
                    job_type=j.job_type.value if hasattr(j.job_type, "value") else str(j.job_type),
                    status=j.status.value if hasattr(j.status, "value") else str(j.status),
                    progress=j.progress,
                    stage=j.stage,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    error_code=j.error_code,
                    error_message=j.error_message,
                    params=j.params if isinstance(j.params, dict) else {},
                    started_at=j.started_at,
                    finished_at=j.finished_at,
                )
                for j in jobs
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Failed to list jobs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list jobs",
        ) from exc


@router.get("/jobs/{job_id}", response_model=JobWithTasksModel)
def get_job(job_id: str) -> JobWithTasksModel:
    """Retrieve job details and associated task records by job_id."""
    from src.db.repo.job_repo import JobRepo  # noqa: PLC0415
    from src.db.repo.task_repo import TaskRepo  # noqa: PLC0415
    try:
        conn = get_connection()
        try:
            job_repo = JobRepo(conn)
            task_repo = TaskRepo(conn)
            job = job_repo.get(job_id)
            if not job:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Job {job_id} not found",
                )
            tasks = task_repo.get_tasks_by_job(job_id)
            return JobWithTasksModel(
                id=job.id,
                project_id=job.project_id,
                job_type=job.job_type.value if hasattr(job.job_type, "value") else str(job.job_type),
                status=job.status.value if hasattr(job.status, "value") else str(job.status),
                progress=job.progress,
                stage=job.stage,
                created_at=job.created_at,
                updated_at=job.updated_at,
                error_code=job.error_code,
                error_message=job.error_message,
                params=job.params if isinstance(job.params, dict) else {},
                started_at=job.started_at,
                finished_at=job.finished_at,
                tasks=[
                    TaskModel(
                        id=t.id,
                        job_id=t.job_id,
                        task_type=t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
                        stage=t.stage,
                        status=t.status.value if hasattr(t.status, "value") else str(t.status),
                        progress=t.progress,
                        depends_on=t.depends_on,
                        created_at=t.created_at,
                        updated_at=t.updated_at,
                        error_code=t.error_code,
                        error_message=t.error_message,
                        started_at=t.started_at,
                        finished_at=t.finished_at,
                    )
                    for t in tasks
                ],
            )
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get job %s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job {job_id}",
        ) from exc



@router.get("/system/hardware")
def system_hardware() -> dict:
    """Hardware profile: GPU, RAM, FFmpeg encoders."""
    import subprocess
    import os

    gpu_name = None
    gpu_vendor = None
    vram_mb = None

    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            gpu_name = parts[0].strip()
            gpu_vendor = "nvidia"
            vram_mb = int(parts[1].strip()) if len(parts) > 1 else None
    except Exception:
        pass

    # RAM
    ram_mb = 0
    try:
        import psutil
        ram_mb = psutil.virtual_memory().total // (1024 * 1024)
    except ImportError:
        try:
            r = subprocess.run(["wmic", "os", "get", "TotalVisibleMemorySize"],
                             capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    ram_mb = int(line) // 1024
                    break
        except Exception:
            pass

    # FFmpeg encoders
    ffmpeg_encoders = []
    try:
        r = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            for enc in ("nvenc", "qsv", "amf", "libx264", "libx265"):
                if enc in line.lower():
                    ffmpeg_encoders.append(enc)
        ffmpeg_encoders = sorted(set(ffmpeg_encoders))
    except Exception:
        pass

    return {
        "gpu_vendor": gpu_vendor,
        "gpu_name": gpu_name,
        "vram_mb": vram_mb,
        "ram_mb": ram_mb,
        "ffmpeg_encoders": ffmpeg_encoders,
    }


@router.get("/worker/state")
def worker_state() -> dict:
    """Worker lifecycle state - always 'ready' when the HTTP server responds."""
    return {
        "state": "ready",
        "pid": os.getpid(),
        "port": 8765,
        "restarts": 0,
        "last_error": None,
    }


