"""Unit tests for Phase 6 DAG & Dependency Resolution Engine."""

import pytest
import json
from src.db.models import Task, TaskType, TaskStatus
from src.orchestration.dag_resolver import DAGResolver, ConcurrencyLimits


def make_task(id: str, task_type: TaskType, deps: list[str], status: TaskStatus = TaskStatus.QUEUED) -> Task:
    return Task(
        id=id,
        job_id="job_1",
        task_type=task_type,
        stage=id,
        status=status,
        progress=0.0,
        depends_on=json.dumps(deps),
        created_at="2026-08-23T00:00:00Z",
        updated_at="2026-08-23T00:00:00Z",
    )


def test_concurrency_limits():
    limits = ConcurrencyLimits(global_limit=2, per_type={"transcribe": 1, "translate": 2})
    running = {"t1": "transcribe"}

    # Cannot spawn second transcribe
    assert not limits.can_spawn("transcribe", running)
    # Can spawn first translate
    assert limits.can_spawn("translate", running)

    running["t2"] = "translate"
    # Global limit (2) reached
    assert not limits.can_spawn("translate", running)


def test_dag_resolver_flow():
    # DAG: A (transcribe) -> B (translate), C (tts) -> D (render)
    a = make_task("a", TaskType.TRANSCRIBE, [])
    b = make_task("b", TaskType.TRANSLATE, ["a"])
    c = make_task("c", TaskType.TTS, ["a"])
    d = make_task("d", TaskType.RENDER, ["b", "c"])

    resolver = DAGResolver([a, b, c, d])

    # Initial ready task is only A
    ready = resolver.get_next_ready_tasks()
    assert [t.id for t in ready] == ["a"]

    # Mark A running, then succeeded
    resolver.running_tasks["a"] = "transcribe"
    next_ready = resolver.mark_task_succeeded("a")
    # B and C now ready
    assert sorted([t.id for t in ready] if (ready := resolver.get_next_ready_tasks()) else []) == ["b", "c"]

    # Succeed B and C
    resolver.mark_task_succeeded("b")
    resolver.mark_task_succeeded("c")

    # D is now ready
    assert [t.id for t in resolver.get_next_ready_tasks()] == ["d"]

    resolver.mark_task_succeeded("d")
    assert not resolver.is_deadlocked()


def test_dag_resolver_failure_blocking():
    a = make_task("a", TaskType.TRANSCRIBE, [])
    b = make_task("b", TaskType.TRANSLATE, ["a"])

    resolver = DAGResolver([a, b])
    resolver.mark_task_failed("a")

    assert resolver.tasks_map["b"].status == TaskStatus.BLOCKED
    assert not resolver.is_deadlocked()
