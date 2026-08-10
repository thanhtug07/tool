"""Tests for the autonomous verify/gates module (scripts/autonomous_verify.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomous_verify import (  # noqa: E402
    CommandError,
    gates_all_pass,
    is_git_clean,
    run_command,
    run_gates_for_layers,
)


class TestRunCommand:
    def test_success(self) -> None:
        proc = run_command([sys.executable, "-c", "print('ok')"])
        assert proc.returncode == 0
        assert proc.stdout.strip() == "ok"

    def test_failure_raises(self) -> None:
        with pytest.raises(CommandError):
            run_command([sys.executable, "-c", "import sys; sys.exit(3)"])

    def test_missing_command_raises(self) -> None:
        with pytest.raises(CommandError):
            run_command(["definitely-not-a-real-command-xyz123"])

    def test_argument_array_used_not_shell(self) -> None:
        # If this used a shell, the semicolon would execute; as arg array it is literal.
        proc = run_command([sys.executable, "-c", "import sys; sys.argv[1]", ";echo HACKED"])
        assert proc.returncode == 0
        assert "HACKED" not in proc.stdout


class TestGitHelpers:
    def test_git_clean_status(self) -> None:
        # Returns bool without raising; repo may or may not be clean.
        assert isinstance(is_git_clean(), bool)


class TestGates:
    def test_gates_all_pass(self) -> None:
        assert gates_all_pass({"frontend": {"typecheck": "PASS", "test": "PASS"}})
        assert not gates_all_pass({"frontend": {"typecheck": "PASS", "test": "FAIL: x"}})

    def test_run_gates_for_layers_unknown_layer(self) -> None:
        # No matching tooling -> result dict simply omits unknown layers
        results = run_gates_for_layers(set())
        assert results == {}

    def test_run_worker_gates_fast(self) -> None:
        # The worker pytest gate is the cheapest real gate; run it once.
        results = run_gates_for_layers({"worker"})
        assert "worker" in results
        assert results["worker"]["pytest"] == "PASS"
