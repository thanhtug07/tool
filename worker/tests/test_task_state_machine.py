"""Unit tests for Phase 5 Task State Machine & DAG Dependency Readiness Evaluator."""

import pytest
import json
from src.db.models import Task, TaskType, TaskStatus
from src.orchestration.task_state_machine import (
    TaskStateMachine,
    TaskEvaluationResult,
    DAGValidationError,
)


def make_test_task(id: str, deps: list[str], status: TaskStatus = TaskStatus.QUEUED) -> Task:
    return Task(
        id=id,
        job_id="job_1",
        task_type=TaskType.TRANSCRIBE,
        stage=id,
        status=status,
        progress=0.0,
        depends_on=json.dumps(deps),
        created_at="2026-08-23T00:00:00Z",
        updated_at="2026-08-23T00:00:00Z",
    )


def test_dependency_free_task_becomes_ready():
    task_a = make_test_task("a", [])
    tasks_map = {"a": task_a}
    completed_ids = set()

    res = TaskStateMachine.evaluate_task_readiness("a", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.READY


def test_task_waits_when_dependency_incomplete():
    task_a = make_test_task("a", [])
    task_b = make_test_task("b", ["a"])
    tasks_map = {"a": task_a, "b": task_b}
    completed_ids = set()  # 'a' not completed

    res = TaskStateMachine.evaluate_task_readiness("b", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.WAITING


def test_task_becomes_ready_after_dependency_succeeded():
    task_a = make_test_task("a", [], status=TaskStatus.SUCCEEDED)
    task_b = make_test_task("b", ["a"])
    tasks_map = {"a": task_a, "b": task_b}
    completed_ids = {"a"}

    res = TaskStateMachine.evaluate_task_readiness("b", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.READY


def test_failed_dependency_blocks_dependent():
    task_a = make_test_task("a", [], status=TaskStatus.FAILED)
    task_b = make_test_task("b", ["a"])
    tasks_map = {"a": task_a, "b": task_b}
    completed_ids = set()

    res = TaskStateMachine.evaluate_task_readiness("b", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.BLOCKED


def test_cancelled_dependency_blocks_dependent():
    task_a = make_test_task("a", [], status=TaskStatus.CANCELLED)
    task_b = make_test_task("b", ["a"])
    tasks_map = {"a": task_a, "b": task_b}
    completed_ids = set()

    res = TaskStateMachine.evaluate_task_readiness("b", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.BLOCKED


def test_blocked_dependency_blocks_dependent():
    task_a = make_test_task("a", [], status=TaskStatus.BLOCKED)
    task_b = make_test_task("b", ["a"])
    tasks_map = {"a": task_a, "b": task_b}
    completed_ids = set()

    res = TaskStateMachine.evaluate_task_readiness("b", tasks_map, completed_ids)
    assert res == TaskEvaluationResult.BLOCKED


def test_terminal_succeeded_protected():
    status = TaskStatus.SUCCEEDED
    assert status.is_terminal()
    assert not status.can_transition(TaskStatus.FAILED)
    assert not status.can_transition(TaskStatus.CANCELLED)
    assert not status.can_transition(TaskStatus.READY)


def test_cyclic_dependency_detected():
    task_a = make_test_task("a", ["b"])
    task_b = make_test_task("b", ["a"])
    tasks = [task_a, task_b]

    with pytest.raises(DAGValidationError, match="Cycle detected"):
        TaskStateMachine.validate_dag(tasks)


def test_unresolved_dependency_detected():
    task_a = make_test_task("a", ["non_existent_id"])
    tasks = [task_a]

    with pytest.raises(DAGValidationError, match="non-existent task"):
        TaskStateMachine.validate_dag(tasks)


def test_transitive_blocking():
    # Graph: A -> B -> C
    task_a = make_test_task("a", [], status=TaskStatus.FAILED)
    task_b = make_test_task("b", ["a"])
    task_c = make_test_task("c", ["b"])
    tasks_map = {"a": task_a, "b": task_b, "c": task_c}

    to_block = TaskStateMachine.compute_transitive_blocks("a", tasks_map)
    assert to_block == {"b", "c"}
