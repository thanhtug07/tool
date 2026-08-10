"""Unit tests for the cancellable subprocess runner (TASK-010).

These exercise the real subprocess machinery against ``sys.executable`` (no
ffmpeg/whisper needed) and the pure ``CancellationToken`` semantics.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from src.core.job import (
    CancelledError,
    CancellationToken,
    ProcessSpawnError,
    ProcessTimeoutError,
    run_process,
)


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


class TestCancellationToken:
    def test_default_is_not_cancelled(self) -> None:
        token = CancellationToken()
        assert not token.is_cancelled()

    def test_cancel_is_idempotent(self) -> None:
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled()

    def test_each_token_is_independent(self) -> None:
        a = CancellationToken()
        b = CancellationToken()
        a.cancel()
        assert a.is_cancelled()
        assert not b.is_cancelled()


class TestRunProcessSuccess:
    def test_clean_run_returns_captured_output(self) -> None:
        result = run_process(_py("print('hello')"))
        assert result.returncode == 0
        assert result.stdout.strip() == b"hello"
        assert result.stderr == b""

    def test_nonzero_exit_is_reported_not_raised(self) -> None:
        result = run_process(_py("import sys; sys.exit(3)"))
        assert result.returncode == 3

    def test_args_are_never_interpreted_by_a_shell(self, tmp_path) -> None:
        # A filename full of shell metacharacters must arrive verbatim.
        evil = tmp_path / "a; b & c.txt"
        evil.write_text("x")
        result = run_process([sys.executable, "-c", "import sys; print(sys.argv[1])", str(evil)])
        assert result.returncode == 0
        assert result.stdout.decode("utf-8").strip() == str(evil)


class TestRunProcessFailure:
    def test_empty_argv_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            run_process([])
        with pytest.raises(ValueError):
            run_process([""])
        with pytest.raises(ValueError):
            run_process("not-a-list")

    def test_missing_executable_raises_spawn_error(self) -> None:
        with pytest.raises(ProcessSpawnError):
            run_process(["definitely-not-a-real-binary-xyz123", "arg"])


class TestRunProcessCancellation:
    def test_cancel_before_spawn_raises_immediately(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancelledError):
            run_process(_py("import time; time.sleep(30)"), cancel=token)

    def test_cancel_mid_run_kills_process_and_raises(self) -> None:
        token = CancellationToken()

        def _cancel_later() -> None:
            time.sleep(0.3)
            token.cancel()

        threading.Thread(target=_cancel_later, daemon=True).start()
        started = time.monotonic()
        with pytest.raises(CancelledError):
            run_process(_py("import time; time.sleep(30)"), cancel=token)
        elapsed = time.monotonic() - started
        assert elapsed < 20, "cancel must interrupt the running process"


class TestRunProcessTimeout:
    def test_timeout_kills_process_and_raises(self) -> None:
        started = time.monotonic()
        with pytest.raises(ProcessTimeoutError):
            run_process(_py("import time; time.sleep(30)"), timeout=0.2)
        elapsed = time.monotonic() - started
        assert elapsed < 20, "timeout must interrupt the running process"

    def test_fast_command_is_not_timed_out(self) -> None:
        result = run_process(_py("print('fast')"), timeout=5.0)
        assert result.returncode == 0


class TestRunProcessHelpers:
    def test_kill_tree_is_idempotent_on_already_exited_process(self) -> None:
        from src.core.job import _kill_tree

        proc = subprocess.Popen(_py("print('x')"), stdout=subprocess.PIPE)
        proc.wait(timeout=10)
        _kill_tree(proc, kill_grace=0.1)  # must not raise
        assert proc.returncode == 0
