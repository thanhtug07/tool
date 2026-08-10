"""Task discovery from ``TASKS.md`` (source of truth for the autonomous runner).

The runner never hardcodes the task list — it parses the actual dependency
graph from ``TASKS.md`` and derives the current task from the persisted state
and git history.
"""

from __future__ import annotations

import re
from pathlib import Path

TASK_HEADER_RE = re.compile(r"^### TASK-(\d{3})\s*\|\s*(.+)$")
DEPENDENCIES_RE = re.compile(r"- \*\*Dependencies:\*\*(.*)$")
FILES_RE = re.compile(r"- \*\*Files/Modules:\*\*(.*)$")

# Layer detection is prefix/extension based per file mention (see ``Task.layers``):
# ``worker/src/...`` -> worker, ``src-tauri/src/...`` -> rust, ``src/...`` -> frontend.


class Task:
    """A single task parsed from ``TASKS.md``."""

    def __init__(self, task_id: str, num: int, title: str, start_line: int) -> None:
        self.id = task_id
        self.num = num
        self.title = title
        self.start_line = start_line
        self.end_line: int | None = None
        self.dependencies: list[str] = []
        self.files_line: str = ""

    @property
    def is_mvp(self) -> bool:
        """TASK-001..TASK-030 are the MVP tasks the runner drives."""
        return 1 <= self.num <= 30

    @property
    def layers(self) -> set[str]:
        """Which layers this task touches (frontend / rust / worker).

        Classification is prefix/extension based per file mention, so a
        ``worker/src/...`` path is worker, not frontend, and ``src-tauri/src/...``
        is rust.
        """
        touched: set[str] = set()
        for token in self.files_line.split(","):
            token = token.strip().strip("`")
            if not token:
                continue
            if token.startswith("worker/") or token.endswith(".py"):
                touched.add("worker")
            elif token.startswith("src-tauri") or token.endswith(".rs"):
                touched.add("rust")
            elif token.startswith("src/") or token.endswith((".tsx", ".ts")):
                touched.add("frontend")
            elif any(kw in token for kw in ("services/", "commands/", "db/")) and "src-tauri" not in token:
                touched.add("rust")
            elif any(kw in token for kw in ("schemas/", "fixtures/", "pytest")):
                touched.add("worker")
        return touched

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.title!r}>"


def discover_tasks(tasks_md: Path) -> list[Task]:
    """Parse every ``### TASK-NNN`` block from ``TASKS.md``."""
    tasks: list[Task] = []
    current: Task | None = None
    for lineno, line in enumerate(tasks_md.read_text(encoding="utf-8").splitlines(), start=1):
        match = TASK_HEADER_RE.match(line.strip())
        if match:
            if current is not None:
                current.end_line = lineno - 1
                tasks.append(current)
            current = Task(
                task_id=f"TASK-{match.group(1)}",
                num=int(match.group(1)),
                title=match.group(2).strip(),
                start_line=lineno,
            )
            continue
        if current is None:
            continue
        stripped = line.strip()
        dep_match = DEPENDENCIES_RE.match(stripped)
        if dep_match:
            current.dependencies = re.findall(r"TASK-\d+", dep_match.group(1))
        files_match = FILES_RE.match(stripped)
        if files_match:
            current.files_line = files_match.group(1).strip()
    if current is not None:
        current.end_line = lineno
        tasks.append(current)
    return tasks


def get_task_section(tasks_md: Path, task: Task) -> str:
    """Return the raw ``TASKS.md`` section for ``task`` (for the agent brief)."""
    if task.end_line is None:
        return ""
    lines = tasks_md.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[task.start_line - 1 : task.end_line])


def find_current_task(
    tasks: list[Task],
    completed_tasks: set[str],
    start_task: str | None = None,
) -> Task | None:
    """Return the next task whose dependencies are satisfied.

    Only MVP tasks (001-030) are considered. ``start_task`` overrides the
    automatic choice when it is a known task that has not been completed yet.
    """
    known = {t.id: t for t in tasks}
    if start_task is not None:
        task = known.get(start_task)
        if task is not None and task.is_mvp and task.id not in completed_tasks:
            return task

    for task in sorted((t for t in tasks if t.is_mvp), key=lambda t: t.num):
        if task.id in completed_tasks:
            continue
        if all(dep in completed_tasks for dep in task.dependencies):
            return task
    return None
