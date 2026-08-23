"""DAG & Dependency Resolution Engine (Phase 6).

Builds task graphs, evaluates node readiness, manages concurrency limits (global & per-type),
and detects dependency deadlocks matching Rust task_runner.rs.
"""

from typing import Dict, List, Set, Optional
import json
from src.db.models import Task, TaskType, TaskStatus
from src.orchestration.task_state_machine import TaskStateMachine, TaskEvaluationResult, DAGValidationError


class ConcurrencyLimits:
    def __init__(self, global_limit: int = 3, per_type: Optional[Dict[str, int]] = None):
        self.global_limit = global_limit
        self.per_type = per_type or {
            "transcribe": 1,
            "translate": 2,
            "subtitle": 2,
            "tts": 2,
            "logo": 1,
            "render": 1,
            "chunk": 1,
            "audio": 1,
        }

    def can_spawn(self, task_type: str, running_tasks: Dict[str, str]) -> bool:
        if len(running_tasks) >= self.global_limit:
            return False
        type_str = task_type.value if hasattr(task_type, "value") else str(task_type)
        limit = self.per_type.get(type_str, 1)
        count = sum(1 for t in running_tasks.values() if (t.value if hasattr(t, "value") else str(t)) == type_str)
        return count < limit


class DAGResolver:
    """Manages DAG execution state, resolving ready tasks while respecting concurrency constraints."""

    def __init__(self, tasks: List[Task], limits: Optional[ConcurrencyLimits] = None):
        TaskStateMachine.validate_dag(tasks)
        self.limits = limits or ConcurrencyLimits()
        self.tasks_map: Dict[str, Task] = {t.id: t for t in tasks}
        self.completed_ids: Set[str] = {t.id for t in tasks if t.status == TaskStatus.SUCCEEDED}
        self.running_tasks: Dict[str, str] = {
            t.id: (t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type))
            for t in tasks
            if t.status == TaskStatus.RUNNING
        }
        self.blocked_ids: Set[str] = {t.id for t in tasks if t.status == TaskStatus.BLOCKED}

    def get_next_ready_tasks(self) -> List[Task]:
        """Find non-running, non-terminal tasks that are ready to run under current concurrency limits."""
        ready_tasks: List[Task] = []
        
        for task_id, task in self.tasks_map.items():
            if task_id in self.running_tasks or task.status.is_terminal() or task.status == TaskStatus.BLOCKED:
                continue

            eval_res = TaskStateMachine.evaluate_task_readiness(task_id, self.tasks_map, self.completed_ids)
            if eval_res == TaskEvaluationResult.READY:
                task_type_str = task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type)
                # Check if spawning this task violates current running concurrency limits
                # Note: Simulate adding candidate tasks to running_tasks for batch checking
                temp_running = dict(self.running_tasks)
                for rt in ready_tasks:
                    rt_type = rt.task_type.value if hasattr(rt.task_type, "value") else str(rt.task_type)
                    temp_running[rt.id] = rt_type

                if self.limits.can_spawn(task_type_str, temp_running):
                    ready_tasks.append(task)

        return ready_tasks

    def mark_task_succeeded(self, task_id: str) -> List[str]:
        """Mark a task succeeded, update completed set, and return newly ready task IDs."""
        if task_id in self.tasks_map:
            self.tasks_map[task_id].status = TaskStatus.SUCCEEDED
            self.tasks_map[task_id].progress = 1.0
        self.completed_ids.add(task_id)
        self.running_tasks.pop(task_id, None)

        return [t.id for t in self.get_next_ready_tasks()]

    def mark_task_failed(self, task_id: str) -> List[str]:
        """Mark a task failed and propagate transitive blocks to dependents. Returns newly blocked task IDs."""
        if task_id in self.tasks_map:
            self.tasks_map[task_id].status = TaskStatus.FAILED
        self.running_tasks.pop(task_id, None)

        newly_blocked = TaskStateMachine.compute_transitive_blocks(task_id, self.tasks_map)
        for b_id in newly_blocked:
            if b_id in self.tasks_map:
                self.tasks_map[b_id].status = TaskStatus.BLOCKED
            self.blocked_ids.add(b_id)

        return list(newly_blocked)

    def is_deadlocked(self) -> bool:
        """Return True if there are remaining non-terminal, non-blocked tasks but no tasks are running or ready."""
        remaining = [
            t.id for t in self.tasks_map.values()
            if not t.status.is_terminal() and t.status != TaskStatus.BLOCKED
        ]
        if not remaining:
            return False

        ready = self.get_next_ready_tasks()
        return len(self.running_tasks) == 0 and len(ready) == 0
