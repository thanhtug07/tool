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

    Besides the cancellation flag it also carries:

    * A best-effort ``progress`` (0..1) and ``stage`` label for the Rust
      orchestrator's progress poller.
    * A FIFO queue of ``(level, message)`` tuples for the chunked pipeline's
      parallel event stream (``set_event`` / ``drain_events``).
    * A structured event log with monotonic ``event_id`` values, typed
      ``event_type`` (progress/cancelled), optional ``chunk_index`` /
      ``total_chunks``, and ISO-8601 timestamps — queried via
      ``get_events_since(cursor)``.

    Jitter collapsing: consecutive calls with the same *stage + message*
    within the same ``set_progress`` invocation update the last event in
    place rather than appending a duplicate.
    """

    EVENT_BUFFER_SIZE: int = 500

    def __init__(self) -> None:
        self._event = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._progress = 0.0
        self._stage = ""
        self._message: str | None = None
        self._events: list[tuple[str, str]] = []  # legacy (level, msg) queue
        # Structured event log
        self._structured: list[dict] = []
        self._next_event_id: int = 1
        # Waiters that should be woken on new events / cancel / close
        self._waiters: list[threading.Event] = []

    # -- lifecycle ---------------------------------------------------------

    def cancel(self) -> None:
        """Request cancellation. Idempotent. Records a terminal event."""
        with self._lock:
            already = self._event.is_set()
            self._event.set()
            if not already:
                self._append_event("cancelled", "", 0.0, message="cancelled")
        self._wake_all()

    def close(self) -> None:
        """Mark the token as closed (graceful shutdown). Idempotent."""
        with self._lock:
            self._closed.set()
        self._wake_all()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def is_closed(self) -> bool:
        return self._closed.is_set()

    # -- progress ----------------------------------------------------------

    def set_progress(
        self,
        progress: float,
        stage: str = "",
        message: str | None = None,
        *,
        chunk_index: int | None = None,
        total_chunks: int | None = None,
    ) -> None:
        """Record stage progress (clamped to 0..1) plus optional labels.

        ``chunk_index`` / ``total_chunks`` are forwarded to the structured
        event log so the frontend can render per-chunk progress bars.
        """
        with self._lock:
            self._progress = max(0.0, min(1.0, float(progress)))
            if stage:
                self._stage = stage
            if message is not None:
                self._message = message
            self._append_event(
                "progress",
                stage or self._stage,
                self._progress,
                message=message,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
            )

    def get_progress(self) -> tuple[float, str, str | None]:
        """Return ``(progress, stage, message)`` as last reported."""
        with self._lock:
            return self._progress, self._stage, self._message

    # -- structured event log ----------------------------------------------

    def _append_event(
        self,
        event_type: str,
        stage: str,
        progress: float,
        message: str | None = None,
        chunk_index: int | None = None,
        total_chunks: int | None = None,
    ) -> None:
        """Append a structured event (caller must hold ``_lock``)."""
        # Normalize message for comparison (None and "" are equivalent).
        norm_msg = message or ""
        # Jitter collapsing: if the last event has the same stage+message,
        # update it in place rather than appending.
        if (
            event_type == "progress"
            and self._structured
            and self._structured[-1]["event_type"] == "progress"
            and self._structured[-1]["stage"] == stage
            and self._structured[-1]["message"] == norm_msg
        ):
            self._structured[-1]["progress"] = progress
            return
        eid = self._next_event_id
        self._next_event_id += 1
        self._structured.append({
            "event_id": eid,
            "event_type": event_type,
            "stage": stage,
            "progress": progress,
            "message": norm_msg,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        })
        # Enforce buffer size
        if len(self._structured) > self.EVENT_BUFFER_SIZE:
            self._structured = self._structured[-self.EVENT_BUFFER_SIZE :]

    def get_events_since(self, cursor: int) -> list[dict]:
        """Return structured events with ``event_id > cursor``.

        ``cursor`` is the ``event_id`` of the last event the caller has
        already seen.  Returns an empty list when there are no new events.
        """
        with self._lock:
            return [e for e in self._structured if e["event_id"] > cursor]

    def last_event_id(self) -> int:
        """Highest ``event_id`` currently in the log (0 if empty)."""
        with self._lock:
            if not self._structured:
                return 0
            return self._structured[-1]["event_id"]

    # -- wait / wakeup -----------------------------------------------------

    def wait_for_event(self, timeout: float | None = None) -> bool:
        """Block until a new event is recorded, cancelled, or closed.

        Returns ``True`` if woken (event / cancel / close), ``False`` on
        timeout.
        """
        evt = threading.Event()
        with self._lock:
            self._waiters.append(evt)
        try:
            return evt.wait(timeout=timeout)
        finally:
            with self._lock:
                try:
                    self._waiters.remove(evt)
                except ValueError:
                    pass

    def _wake_all(self) -> None:
        """Wake all threads blocked in ``wait_for_event``.

        Copies the waiter list under the lock then signals outside it to
        avoid holding the lock while唤醒 potentially re-registering threads.
        """
        with self._lock:
            waiters = self._waiters[:]
        for w in waiters:
            w.set()

    # -- legacy chunked-pipeline event queue -------------------------------

    def set_event(self, level: str, message: str) -> None:
        """Enqueue a log event for the chunked pipeline's parallel event stream.

        Unlike ``set_progress`` which overwrites the single ``_message``,
        events accumulate in a FIFO queue so the Rust poller can drain them
        all without losing intermediate chunk-started / chunk-assembled lines.
        """
        with self._lock:
            self._events.append((level, message))
        self._wake_all()

    def drain_events(self) -> list[tuple[str, str]]:
        """Return and clear all pending events (thread-safe).

        The Rust ``/v1/progress`` poller calls this to collect every event
        that was enqueued since the last drain.  Returns an empty list when
        no new events are pending.
        """
        with self._lock:
            events = self._events[:]
            self._events.clear()
            return events


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
