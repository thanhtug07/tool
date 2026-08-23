"""Task State Machine Core (Phase 5).

Pure task state machine & DAG dependency readiness evaluator based strictly on verified Rust TaskRunner behavior.
Does NOT execute real worker stages, run subprocesses, or handle thread concurrency.
"""

import json
import logging
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple, Optional, Any
from enum import Enum

from src.db.models import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskEvaluationResult(str, Enum):
    READY = "ready"
    WAITING = "waiting"
    BLOCKED = "blocked"


class DAGValidationError(Exception):
    """Raised when DAG structure validation fails (duplicate ID, missing dep, cycle)."""


class TaskStateMachine:
    """Pure evaluator for task dependency readiness, state transition validation, and DAG deadlock detection."""

    @staticmethod
    def validate_dag(tasks: List[Task]) -> None:
        """Validate DAG structural integrity: unique task IDs, existing dependencies, no cycles."""
        seen_ids: Set[str] = set()
        task_ids: Set[str] = {t.id for t in tasks}

        for task in tasks:
            if task.id in seen_ids:
                raise DAGValidationError(f"Duplicate task ID: {task.id}")
            seen_ids.add(task.id)

            try:
                deps: List[str] = json.loads(task.depends_on) if task.depends_on else []
            except Exception as e:
                raise DAGValidationError(f"Invalid depends_on for task {task.id}: {e}") from e

            for dep in deps:
                if dep not in task_ids:
                    raise DAGValidationError(f"Task {task.id} depends on non-existent task: {dep}")

        # Cycle detection via Kahn's algorithm (topological sort)
        in_degree: Dict[str, int] = {t.id: 0 for t in tasks}
        adj: Dict[str, List[str]] = defaultdict(list)

        for task in tasks:
            deps = json.loads(task.depends_on) if task.depends_on else []
            for dep in deps:
                adj[dep].append(task.id)
                in_degree[task.id] += 1

        queue: deque = deque([tid for tid, deg in in_degree.items() if deg == 0])
        processed_count = 0

        while queue:
            node = queue.popleft()
            processed_count += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed_count != len(tasks):
            raise DAGValidationError("Cycle detected in task dependency graph")

    @staticmethod
    def evaluate_task_readiness(
        task_id: str,
        tasks_map: Dict[str, Task],
        completed_ids: Set[str],
    ) -> TaskEvaluationResult:
        """Evaluate if a task is READY, WAITING for dependencies, or BLOCKED by a failed dependency.

        Rules matching Rust reference:
        - A task is READY if all of its dependencies have SUCCEEDED (are in completed_ids).
        - A task is BLOCKED if any of its dependencies is FAILED, CANCELLED, or BLOCKED.
        - Otherwise, the task is WAITING for dependencies to finish.
        """
        task = tasks_map.get(task_id)
        if not task:
            return TaskEvaluationResult.BLOCKED

        deps: List[str] = json.loads(task.depends_on) if task.depends_on else []

        for dep_id in deps:
            dep_task = tasks_map.get(dep_id)
            if not dep_task:
                return TaskEvaluationResult.BLOCKED

            # If dependency is terminally failed, cancelled, or blocked -> dependent is BLOCKED
            if dep_task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED):
                return TaskEvaluationResult.BLOCKED

            # If dependency is not yet completed (succeeded) -> dependent is WAITING
            if dep_id not in completed_ids:
                return TaskEvaluationResult.WAITING

        return TaskEvaluationResult.READY

    @staticmethod
    def compute_transitive_blocks(
        failed_or_cancelled_id: str,
        tasks_map: Dict[str, Task],
    ) -> Set[str]:
        """Compute the set of non-terminal dependent task IDs that must be marked BLOCKED transitively."""
        # Build reverse adjacency map (dep_id -> list of dependent task_ids)
        reverse_adj: Dict[str, List[str]] = defaultdict(list)
        for t in tasks_map.values():
            deps: List[str] = json.loads(t.depends_on) if t.depends_on else []
            for d in deps:
                reverse_adj[d].append(t.id)

        queue: deque = deque([failed_or_cancelled_id])
        visited: Set[str] = set()
        to_block: Set[str] = set()

        while queue:
            curr_id = queue.popleft()
            if curr_id in visited:
                continue
            visited.add(curr_id)

            dependents = reverse_adj.get(curr_id, [])
            for dep_id in dependents:
                dep_task = tasks_map.get(dep_id)
                if dep_task:
                    # Only non-terminal, non-blocked tasks get added to to_block
                    if not dep_task.status.is_terminal() and dep_task.status != TaskStatus.BLOCKED:
                        to_block.add(dep_id)
                    queue.append(dep_id)

        return to_block
