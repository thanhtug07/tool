"""Unit tests for Python DB layer (migrations v1..v9 and repositories)."""

import os
import tempfile
import pytest
from pathlib import Path

from src.db.connection import get_connection, Database
from src.db.migrations import run_migrations, current_version, MIGRATIONS
from src.db.models import (
    Project,
    ProjectStatus,
    Job,
    JobType,
    JobStatus,
    Task,
    TaskType,
    TaskStatus,
    SubtitleCue,
    GlossaryEntry,
    CharacterEntry,
    Provider,
)
from src.db.repo import (
    ProjectRepo,
    JobRepo,
    TaskRepo,
    SubtitleRepo,
    GlossaryRepo,
    CharacterRepo,
    SettingsRepo,
    ProviderRepo,
)


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_app.db"
        conn = get_connection(db_path)
        run_migrations(conn)
        yield conn
        conn.close()


def test_migrations_fresh_db_reaches_version_9(temp_db):
    ver = current_version(temp_db)
    assert ver == len(MIGRATIONS)
    assert ver == 9


def test_migrations_idempotency(temp_db):
    run_migrations(temp_db)
    ver = current_version(temp_db)
    assert ver == 9


def test_project_repo_crud(temp_db):
    repo = ProjectRepo(temp_db)
    p = Project(
        id="proj_1",
        name="Test Project",
        source_video_path="C:\\Videos\\sample.mp4",
        status=ProjectStatus.DRAFT,
        created_at="2026-08-23T10:00:00Z",
        updated_at="2026-08-23T10:00:00Z",
    )
    repo.insert(p)

    loaded = repo.get("proj_1")
    assert loaded is not None
    assert loaded.name == "Test Project"
    assert loaded.status == ProjectStatus.DRAFT

    # find_by_source_path with normalized separator / case
    match = repo.find_by_source_path("c:/videos/sample.mp4")
    assert match is not None
    assert match.id == "proj_1"

    p.name = "Updated Project Name"
    p.status = ProjectStatus.TRANSCRIBED
    assert repo.update(p) is True

    loaded2 = repo.get("proj_1")
    assert loaded2.name == "Updated Project Name"
    assert loaded2.status == ProjectStatus.TRANSCRIBED

    all_projs = repo.list()
    assert len(all_projs) == 1

    assert repo.delete("proj_1") is True
    assert repo.get("proj_1") is None


def test_job_repo_crud_and_next_id(temp_db):
    p_repo = ProjectRepo(temp_db)
    p_repo.insert(
        Project(
            id="proj_1",
            name="P1",
            source_video_path="v.mp4",
            status=ProjectStatus.DRAFT,
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )
    )

    repo = JobRepo(temp_db)
    next_id = repo.next_id()
    assert next_id == "job_0001"

    job = Job(
        id=next_id,
        project_id="proj_1",
        job_type=JobType.TRANSCRIBE,
        status=JobStatus.QUEUED,
        progress=0.0,
        stage="queued",
        created_at="2026-08-23T10:00:00Z",
        updated_at="2026-08-23T10:00:00Z",
        params={"model": "large-v3"},
    )
    repo.insert(job)

    assert repo.next_id() == "job_0002"

    loaded = repo.get("job_0001")
    assert loaded is not None
    assert loaded.job_type == JobType.TRANSCRIBE
    assert loaded.params == {"model": "large-v3"}

    queued = repo.list_queued()
    assert len(queued) == 1
    assert queued[0].id == "job_0001"

    job.status = JobStatus.RUNNING
    job.progress = 0.5
    job.stage = "transcribing"
    assert repo.update(job) is True

    running = repo.list_running()
    assert len(running) == 1
    assert running[0].id == "job_0001"


def test_task_repo_and_transitions(temp_db):
    p_repo = ProjectRepo(temp_db)
    p_repo.insert(
        Project(
            id="proj_1",
            name="P1",
            source_video_path="v.mp4",
            status=ProjectStatus.DRAFT,
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )
    )
    j_repo = JobRepo(temp_db)
    j_repo.insert(
        Job(
            id="job_0001",
            project_id="proj_1",
            job_type=JobType.TRANSCRIBE,
            status=JobStatus.RUNNING,
            progress=0.0,
            stage="running",
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )
    )

    t_repo = TaskRepo(temp_db)
    task = Task(
        id="task_1",
        job_id="job_0001",
        task_type=TaskType.TRANSCRIBE,
        stage="transcribe",
        status=TaskStatus.QUEUED,
        progress=0.0,
        depends_on="[]",
        created_at="2026-08-23T10:00:00Z",
        updated_at="2026-08-23T10:00:00Z",
    )
    t_repo.create_task(task)

    tasks = t_repo.get_tasks_by_job("job_0001")
    assert len(tasks) == 1

    # Transition QUEUED -> READY
    assert t_repo.update_task_status("task_1", TaskStatus.READY, "2026-08-23T10:01:00Z") is True
    # Transition READY -> RUNNING
    assert t_repo.update_task_status("task_1", TaskStatus.RUNNING, "2026-08-23T10:02:00Z") is True
    # Invalid transition RUNNING -> BLOCKED
    assert t_repo.update_task_status("task_1", TaskStatus.BLOCKED, "2026-08-23T10:03:00Z") is False

    assert t_repo.update_task_progress("task_1", 0.75, "2026-08-23T10:04:00Z") is True
    loaded = t_repo.get_task("task_1")
    assert loaded.progress == 0.75


def test_subtitle_repo(temp_db):
    p_repo = ProjectRepo(temp_db)
    p_repo.insert(
        Project(
            id="proj_1",
            name="P1",
            source_video_path="v.mp4",
            status=ProjectStatus.DRAFT,
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )
    )

    s_repo = SubtitleRepo(temp_db)
    cues = [
        SubtitleCue(
            id="cue_1",
            project_id="proj_1",
            cue_number=1,
            start=0.0,
            end=2.5,
            text="Hello world",
            updated_at="2026-08-23T10:00:00Z",
        ),
        SubtitleCue(
            id="cue_2",
            project_id="proj_1",
            cue_number=2,
            start=2.5,
            end=5.0,
            text="Second line",
            updated_at="2026-08-23T10:00:00Z",
        ),
    ]
    s_repo.insert_many(cues)

    loaded_cues = s_repo.list("proj_1")
    assert len(loaded_cues) == 2
    assert loaded_cues[0].text == "Hello world"

    updated = s_repo.update("cue_1", 0.0, 3.0, "Hello edited world", "Speaker A", "edited", "2026-08-23T10:05:00Z")
    assert updated is not None
    assert updated.text == "Hello edited world"
    assert updated.speaker == "Speaker A"

    s_repo.delete_project("proj_1")
    assert len(s_repo.list("proj_1")) == 0


def test_glossary_repo_and_fnv1a_fingerprint(temp_db):
    p_repo = ProjectRepo(temp_db)
    p_repo.insert(
        Project(
            id="proj_1",
            name="P1",
            source_video_path="v.mp4",
            status=ProjectStatus.DRAFT,
            created_at="2026-08-23T10:00:00Z",
            updated_at="2026-08-23T10:00:00Z",
        )
    )

    g_repo = GlossaryRepo(temp_db)
    fp_empty = g_repo.fingerprint("proj_1")
    assert fp_empty == "cbf29ce484222325"

    g_repo.upsert(GlossaryEntry(project_id="proj_1", term="API", translation="Giao diện", updated_at="2026-08-23T10:00:00Z"))
    fp_1 = g_repo.fingerprint("proj_1")
    assert fp_1 != fp_empty

    g_repo.upsert(GlossaryEntry(project_id="proj_1", term="Render", translation="Xuất video", updated_at="2026-08-23T10:00:00Z"))
    fp_2 = g_repo.fingerprint("proj_1")
    assert fp_2 != fp_1

    entries = g_repo.list("proj_1")
    assert len(entries) == 2
    assert entries[0].term == "api"  # normalized to lowercase


def test_settings_repo_defaults_and_validation(temp_db):
    s_repo = SettingsRepo(temp_db)
    all_settings = s_repo.get_all()

    assert all_settings["ai.model"] == "large-v3"
    assert all_settings["ai.device"] == "auto"
    assert all_settings["cache.quota_bytes"] == 10737418240
    assert all_settings["privacy.telemetry"] is False

    # Valid setting update
    updated_all = s_repo.set("ai.device", "cuda", "2026-08-23T10:00:00Z")
    assert updated_all["ai.device"] == "cuda"

    # Invalid setting update
    with pytest.raises(ValueError):
        s_repo.set("ai.device", "invalid_device", "2026-08-23T10:00:00Z")


def test_provider_repo_seed_data(temp_db):
    p_repo = ProviderRepo(temp_db)
    providers = p_repo.list()
    assert len(providers) == 4

    free_prov = p_repo.get("free")
    assert free_prov is not None
    assert free_prov.name == "FREE"

    default_tr = p_repo.get_default("translation")
    assert default_tr is not None
    assert default_tr.id == "free"

    with pytest.raises(ValueError):
        p_repo.delete("free")
