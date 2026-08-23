"""Unit tests for Phase 8 Pipeline Orchestrator."""

from src.db.models import JobType, TaskType
from src.services.job_service import JobDomainService
from src.orchestration.pipeline_orchestrator import PipelineOrchestrator
from src.db.connection import get_connection
from src.db.repo.project_repo import ProjectRepo
from src.db.models import Project, ProjectStatus


def test_pipeline_orchestrator_execution():
    import uuid
    pid = f"p_orch_{uuid.uuid4().hex[:6]}"
    conn = get_connection()
    p_repo = ProjectRepo(conn)
    p_repo.insert(Project(pid, "Orch Proj", "test.mp4", ProjectStatus.DRAFT, "2026-08-23T00:00:00Z", "2026-08-23T00:00:00Z"))
    conn.commit()
    conn.close()

    t1_id = f"t_stt_{uuid.uuid4().hex[:6]}"
    t2_id = f"t_tr_{uuid.uuid4().hex[:6]}"
    service = JobDomainService()
    job = service.create_job(
        pid,
        JobType.TRANSCRIBE,
        tasks_spec=[
            {"id": t1_id, "task_type": "transcribe", "stage": "stt"},
            {"id": t2_id, "task_type": "translate", "stage": "translate", "depends_on": f'["{t1_id}"]'},
        ],
    )

    orchestrator = PipelineOrchestrator(service)
    success = orchestrator.run_job_sync(job.id)
    assert success

    updated_job = service.get_job(job.id)
    assert updated_job.status.value == "succeeded"
