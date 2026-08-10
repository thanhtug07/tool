"""Tests for the autonomous runner orchestrator (scripts/autonomous_runner.py).

These tests never launch a real coding agent — the agent runner is mocked and
subprocess execution paths are exercised only with harmless commands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autonomous_runner  # noqa: E402
from autonomous_state import _empty_state  # noqa: E402
from autonomous_tasks import discover_tasks  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_docs(tmp_path: Path, monkeypatch) -> None:
    """Redirect all runner state files to a temp dir per test."""
    for attr in (
        "PROGRESS_PATH",
        "HANDOFF_PATH",
        "BLOCKERS_PATH",
        "FINAL_REPORT_PATH",
        "STOP_FLAG_PATH",
    ):
        monkeypatch.setattr(autonomous_runner, attr, tmp_path / Path(attr).name)
    monkeypatch.setattr(autonomous_runner, "CONFIG_PATH", tmp_path / "config.json")


class TestConfig:
    def test_default_config_when_missing(self, _isolate_docs, tmp_path: Path) -> None:
        config = autonomous_runner.load_config()
        assert config["max_task_retries"] == 5
        assert config["agent_command"] is None

    def test_loads_existing_config(self, _isolate_docs, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text(
            json.dumps({"agent_command": ["opencode", "run"], "max_task_retries": 2}),
            encoding="utf-8",
        )
        config = autonomous_runner.load_config()
        assert config["agent_command"] == ["opencode", "run"]
        assert config["max_task_retries"] == 2
        assert config["max_session_restarts"] == 20  # defaults merged

    def test_malformed_config_falls_back(self, _isolate_docs, tmp_path: Path) -> None:
        (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
        config = autonomous_runner.load_config()
        assert config["max_task_retries"] == 5


class TestDetectAgent:
    def test_configured_string_command(self, _isolate_docs) -> None:
        agent = autonomous_runner.detect_agent({"agent_command": "opencode"})
        assert agent == ["opencode"]

    def test_configured_list_command(self, _isolate_docs) -> None:
        agent = autonomous_runner.detect_agent({"agent_command": ["my-agent", "--flag"]})
        assert agent == ["my-agent", "--flag"]

    def test_no_configured_agent_returns_empty_when_none_installed(self, _isolate_docs, monkeypatch) -> None:
        monkeypatch.setattr(autonomous_runner, "AGENT_CLIS", [])
        agent = autonomous_runner.detect_agent({"agent_command": None})
        assert agent == []


class TestRunAgent:
    def test_invokes_agent_with_prompt_arg(self, _isolate_docs) -> None:
        proc = autonomous_runner.run_agent(
            [sys.executable, "-c", "import sys; sys.exit(0 if 'BRIEF' in sys.argv[-1] else 1)"],
            "BRIEF: implement task",
        )
        assert proc == 0

    def test_nonzero_exit_propagates(self, _isolate_docs) -> None:
        proc = autonomous_runner.run_agent(
            [sys.executable, "-c", "import sys; sys.exit(7)"],
            "prompt",
        )
        assert proc == 7

    def test_missing_command_returns_127(self, _isolate_docs) -> None:
        proc = autonomous_runner.run_agent(["no-such-agent-xyz"], "prompt")
        assert proc == 127


class TestPromptBuild:
    def test_build_prompt_includes_task_and_template(self, _isolate_docs, tmp_path: Path) -> None:
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "### TASK-010 | Job system\n- **Dependencies:** TASK-008\n",
            encoding="utf-8",
        )
        task = discover_tasks(tasks_md)[0]
        template = tmp_path / "prompt.md"
        template.write_text("# MASTER\n", encoding="utf-8")
        config = {"prompt_template": str(template)}
        prompt = autonomous_runner.build_prompt(config, task, "### TASK-010 | Job system\n")
        assert "# MASTER" in prompt
        assert "TASK-010" in prompt
        assert "Job system" in prompt


class TestGitDerivedState:
    def test_derive_completed_from_git(self, _isolate_docs) -> None:
        ids = autonomous_runner.derive_completed_from_git()
        assert "TASK-009" in ids
        assert all(t.startswith("TASK-") for t in ids)


class TestStateFlow:
    def test_ensure_state_creates_when_missing(self, _isolate_docs, tmp_path: Path) -> None:
        state = autonomous_runner.ensure_state({"start_task": None})
        assert "completed_tasks" in state
        assert autonomous_runner.PROGRESS_PATH.exists()

    def test_write_blocker_records_entries(self, _isolate_docs, tmp_path: Path) -> None:
        autonomous_runner.write_blocker("TASK-010", "decision needed", "evidence.txt")
        data = json.loads(autonomous_runner.BLOCKERS_PATH.read_text(encoding="utf-8"))
        assert data["blockers"][0]["task"] == "TASK-010"
        assert data["blockers"][0]["blocker"] == "decision needed"

    def test_write_final_report(self, _isolate_docs, tmp_path: Path) -> None:
        state = _empty_state()
        state["completed_tasks"] = ["TASK-001", "TASK-010"]
        autonomous_runner.write_final_report(state)
        data = json.loads(autonomous_runner.FINAL_REPORT_PATH.read_text(encoding="utf-8"))
        assert data["status"] == "COMPLETE"
        assert "TASK-010" in data["tasks_completed"]

    def test_stop_flag(self, _isolate_docs, tmp_path: Path, monkeypatch) -> None:
        flag = autonomous_runner.STOP_FLAG_PATH
        assert not flag.exists()
        monkeypatch.setattr(sys, "argv", ["autonomous_runner.py", "--stop"])
        assert autonomous_runner.main() == 0
        assert flag.exists()
        # running again (not --stop) will clear and honor the flag
        monkeypatch.setattr(sys, "argv", ["autonomous_runner.py"])
        # guard: if a coding agent would be launched, do not run the loop
        monkeypatch.setattr(autonomous_runner, "detect_agent", lambda config: [])
        assert autonomous_runner.main() in (0, 3)
        assert not flag.exists()
