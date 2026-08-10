"""Tests for the autonomous task discovery module (scripts/autonomous_tasks.py)."""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomous_tasks import (  # noqa: E402
    Task,
    discover_tasks,
    find_current_task,
    get_task_section,
)

SAMPLE = """# TASKS.md

### TASK-001 | Init repo
- **Dependencies:** —
- **Files/Modules:** `README.md`, `.gitignore`
  Goal — something

### TASK-005 | Worker skeleton
- **Dependencies:** TASK-001
- **Files/Modules:** `worker/` (pyproject.toml)
  Goal — something

### TASK-010 | Job system
- **Dependencies:** TASK-005
- **Files/Modules:** `src-tauri/src/services/job_service.rs`, `worker/src/core/job.py`
  Goal — something

### TASK-099 | Post-MVP (not in 30)
- **Dependencies:** TASK-010
"""


def _write_sample(tmp_path: Path) -> Path:
    path = tmp_path / "TASKS.md"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_discover_tasks(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    ids = [t.id for t in tasks]
    assert ids == ["TASK-001", "TASK-005", "TASK-010", "TASK-099"]
    assert tasks[0].title == "Init repo"
    assert tasks[0].num == 1
    assert tasks[1].dependencies == ["TASK-001"]
    assert tasks[2].dependencies == ["TASK-005"]


def test_is_mvp(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    mvp = [t.id for t in tasks if t.is_mvp]
    assert mvp == ["TASK-001", "TASK-005", "TASK-010"]
    assert all(t.is_mvp for t in tasks if t.num <= 30)


def test_layers_detection(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    by_id = {t.id: t for t in tasks}
    assert by_id["TASK-005"].layers == {"worker"}
    assert by_id["TASK-010"].layers == {"rust", "worker"}


def test_find_current_task_dependency_order(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    current = find_current_task(tasks, set())
    assert current.id == "TASK-001"

    current = find_current_task(tasks, {"TASK-001"})
    assert current.id == "TASK-005"

    current = find_current_task(tasks, {"TASK-001", "TASK-005"})
    assert current.id == "TASK-010"


def test_find_current_task_skips_blocked_dependency(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    # TASK-005 not done -> TASK-010 blocked, even though 001 done
    current = find_current_task(tasks, {"TASK-001", "TASK-099"})
    assert current.id == "TASK-005"


def test_find_current_task_none_when_all_done(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    current = find_current_task(tasks, {"TASK-001", "TASK-005", "TASK-010", "TASK-099"})
    assert current is None


def test_start_task_override(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    current = find_current_task(tasks, {"TASK-001"}, start_task="TASK-010")
    assert current.id == "TASK-010"


def test_get_task_section(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    section = get_task_section(_write_sample(tmp_path), tasks[2])
    assert "### TASK-010 | Job system" in section
    assert "Dependencies:" in section
    assert "Job system" in section
    assert "### TASK-099" not in section


def test_task_representation(tmp_path: Path) -> None:
    tasks = discover_tasks(_write_sample(tmp_path))
    assert "TASK-010" in repr(tasks[2])
