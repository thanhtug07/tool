"""Cancellable subprocess execution for pipeline jobs (TASK-010).

The Rust ``JobService`` orchestrates jobs and cancels them through a polled
flag. Python worker stages must react promptly: an in-flight ffmpeg/whisper
subprocess is killed (the whole process tree) the moment cancellation is
requested. This module provides the shared primitives:

- ``CancellationToken`` — a thread-safe, pollable cancellation signal.
- ``run_process`` — run an argument-array command with cancellation + timeout,
  killing the process tree on cancellation/timeout, and returning a
  ``subprocess.CompletedProcess``.
- ``CancelledError`` / ``ProcessTimeoutError`` / ``ProcessSpawnError``.

Security model
--------------
- Commands are argument arrays only; the child is spawned directly (never a
  shell string), so shell metacharacters in paths are inert.
- Error/log messages never embed the raw command line or input paths.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# How long to wait after SIGTERM before escalating to SIGKILL (POSIX), and how
# long to wait for the tree to die on Windows before falling back to kill().
DEFAULT_KILL_GRACE = 5.0


class CancelledError(Exception):
    """Raised by ``run_process`` when cancellation wins."""


class ProcessTimeoutError(Exception):
    """Raised by ``run_process`` when the command exceeds ``timeout``."""

    def __init__(self, timeout: float) -> None:
        super().__init__(f"command timed out after {timeout:g}s")
        self.timeout = timeout


class ProcessSpawnError(Exception):
    """The command executable could not be started (e.g. missing from PATH)."""


class CancellationToken:
    """A thread-safe cancellation flag shared between the service and a job.

    Besides the cancellation flag it also carries a best-effort ``progress``
    (0..1 within the current stage) and a ``stage`` label so the Rust
    orchestrator can poll live stage progress while a long operation runs.
    Updates are guarded by a lock because the same token is read by the
    ``/v1/progress`` route and written by the stage's ``on_progress`` callback
    from different worker threads.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._progress = 0.0
        self._stage = ""

    def cancel(self) -> None:
        """Request cancellation. Idempotent."""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def set_progress(self, progress: float, stage: str = "") -> None:
        """Record stage progress (clamped to 0..1) plus an optional stage label."""
        with self._lock:
            self._progress = max(0.0, min(1.0, float(progress)))
            if stage:
                self._stage = stage

    def get_progress(self) -> tuple[float, str]:
        """Return ``(progress, stage)`` as last reported."""
        with self._lock:
            return self._progress, self._stage


def run_process(
    argv: list[str],
    *,
    cancel: CancellationToken | None = None,
    timeout: float | None = None,
    kill_grace: float = DEFAULT_KILL_GRACE,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``argv`` (never a shell string) with cancellation and timeout support.

    Returns a ``CompletedProcess`` on a clean wait (any exit code, including
    non-zero — callers inspect ``returncode``). Raises:

    - ``CancelledError`` when ``cancel`` fires while the command is running
      (the process tree is killed first).
    - ``ProcessTimeoutError`` when ``timeout`` seconds elapse first.
    - ``ProcessSpawnError`` when the executable cannot be started.

    ``cancel`` is optional so the primitive is easy to use in isolation; an
    inert token is substituted when omitted.
    """
    if not isinstance(argv, list) or not argv or not argv[0]:
        raise ValueError("argv must be a non-empty argument array")
    if cancel is None:
        cancel = CancellationToken()

    proc = _spawn(argv, capture_output)
    logger.debug("running %s with %d arguments (paths redacted)", argv[0], len(argv))

    # Reaper: as soon as cancellation fires, kill the tree from another thread
    # because Popen.communicate blocks the calling thread on stdout/stderr.
    def _reap() -> None:
        while True:
            if cancel.is_cancelled():
                _kill_tree(proc, kill_grace)
                return
            if proc.poll() is not None:
                return
            time.sleep(0.02)

    reaper = threading.Thread(target=_reap, name="job-cancel-reaper", daemon=True)
    reaper.start()

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc, kill_grace)
        stdout, stderr = proc.communicate()
        raise ProcessTimeoutError(timeout) from None
    finally:
        # Give the reaper a bounded window to observe the terminal state.
        reaper.join(timeout=0.2)

    if cancel.is_cancelled():
        # Cancellation raced the process's own exit; cancel wins (mirrors the
        # Rust JobService cancelled-race handling).
        raise CancelledError()

    return subprocess.CompletedProcess(
        args=argv,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _spawn(argv: list[str], capture_output: bool) -> subprocess.Popen:
    kwargs: dict = {}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if os.name == "nt":
        # A new process group lets us kill the whole tree, and NO_WINDOW keeps
        # spawned binaries from flashing console windows.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        logger.warning("failed to start command executable: %s", exc)
        raise ProcessSpawnError("command executable could not be started.") from exc


def _kill_tree(proc: subprocess.Popen, kill_grace: float) -> None:
    """Kill ``proc`` and its whole tree. Idempotent; never raises."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            _kill_tree_windows(proc)
        else:
            _kill_tree_posix(proc, kill_grace)
    except OSError:  # process already gone or the group no longer exists
        return


def _kill_tree_windows(proc: subprocess.Popen) -> None:
    """``taskkill /T /F`` kills the tree; fall back to ``proc.kill()``."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("taskkill failed for pid=%s; falling back to kill()", proc.pid)
        try:
            proc.kill()
        except OSError:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _kill_tree_posix(proc: subprocess.Popen, kill_grace: float) -> None:
    """SIGTERM the group, wait ``kill_grace``, then escalate to SIGKILL."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=kill_grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=kill_grace)
    except subprocess.TimeoutExpired:
        logger.warning("pid=%s did not exit after SIGKILL", proc.pid)
