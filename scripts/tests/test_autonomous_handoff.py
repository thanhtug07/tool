"""Tests for the autonomous handoff generator (scripts/autonomous_handoff.py)."""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomous_handoff import write_handoff  # noqa: E402
from autonomous_state import _empty_state  # noqa: E402


def test_write_handoff_generates_resume_document(tmp_path: Path, monkeypatch) -> None:
    import autonomous_handoff

    monkeypatch.setattr(autonomous_handoff, "HANDOFF_FILE", tmp_path / "HANDOFF.md")

    state = _empty_state()
    state["current_task"] = "TASK-010"
    state["current_status"] = "IMPLEMENTING"
    state["completed_tasks"] = ["TASK-001", "TASK-009"]
    state["next_task"] = "TASK-011"
    state["last_commit"] = "2a10d65"
    state["retry_count"] = 2

    text = write_handoff(
        state,
        task_section="### TASK-010 | Job system\n- **Dependencies:** TASK-008",
        files_changed=["src-tauri/src/services/job_service.rs"],
        tests_executed=["cargo test", "npm run test"],
        current_failures=["none"],
        next_action="Implement the JobService state machine.",
        context_near_limit=True,
    )

    path = tmp_path / "HANDOFF.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "TASK-010" in content
    assert "Job system" in content
    assert "job_service.rs" in content
    assert "cargo test" in content
    assert "Implement the JobService state machine." in content
    assert "Context near limit" in content
    assert "true" in content
    assert "MASTER_PLAN.md" in content
    assert content == text


def test_handoff_without_optional_args(tmp_path: Path, monkeypatch) -> None:
    import autonomous_handoff

    monkeypatch.setattr(autonomous_handoff, "HANDOFF_FILE", tmp_path / "HANDOFF.md")
    state = _empty_state()
    state["current_task"] = "TASK-011"
    text = write_handoff(state)
    assert "(none)" in text
    assert "TASK-011" in text
