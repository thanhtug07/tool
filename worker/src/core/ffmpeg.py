"""Safe FFmpeg helpers (TASK-012).

- **Argument-array only**: FFmpeg is always launched with a list of arguments,
  never a shell command string, so shell metacharacters in file names are inert.
- **Path validation** rejects NUL and the shell metacharacters ``; | & \\n`` as
  defense-in-depth (TASK-012 requirement) even though argv already neutralizes
  them.
- **Executable allowlist**: ``FFMPEG_BIN`` may only name ``ffmpeg``/``ffmpeg.exe``
  (bare or an existing path whose basename matches) — it can never point at an
  arbitrary executable.
- **Progress**: ``-progress pipe:1`` key/value lines are parsed into a plain
  dict and streamed to ``run_ffmpeg``'s ``on_progress`` callback.
- **Cancellation / timeout**: mirrors the process-tree kill semantics of
  ``src.core.job`` (taskkill /T /F on Windows, SIGTERM -> grace -> SIGKILL on
  POSIX).

Error taxonomy (MASTER_PLAN §28.1): ``E_FFMPEG_NOT_FOUND`` when the binary is
missing; ``E_FFMPEG_FAILED`` for any ffmpeg failure.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from src.core.job import (
    DEFAULT_KILL_GRACE,
    CancelledError,
    CancellationToken,
    ProcessTimeoutError,
    _kill_tree,
)

logger = logging.getLogger(__name__)

E_FFMPEG_NOT_FOUND = "E_FFMPEG_NOT_FOUND"
E_FFMPEG_FAILED = "E_FFMPEG_FAILED"

# Allowlisted executable names (bare PATH lookup or explicit path basename).
FFMPEG_ALLOWLIST = frozenset({"ffmpeg", "ffmpeg.exe"})

# Characters rejected in file paths (defense-in-depth, not a security boundary).
_FORBIDDEN_IN_PATH = frozenset({";", "|", "&", "\n", "\0"})

# Canonical audio extract spec (MASTER_PLAN §13 / ARCHITECTURE_DECISION §3.7):
# 16 kHz mono PCM.
DEFAULT_EXTRACT_SAMPLE_RATE = 16000
DEFAULT_EXTRACT_CHANNELS = 1
DEFAULT_EXTRACT_CODEC = "pcm_s16le"

# ffmpeg stderr markers that mean "no audio stream to extract".
_NO_AUDIO_MARKERS = (
    "does not contain any stream",
    "does not contain a stream",
    "no audio",
    "output file does not contain",
)


class FFmpegError(Exception):
    """FFmpeg failure carrying the architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class FFmpegResult:
    """Outcome of a finished ffmpeg run."""

    returncode: int
    stderr: str
    out_time_us: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.out_time_us / 1_000_000


def resolve_ffmpeg() -> str:
    """Resolve the ffmpeg executable through the allowlist.

    1. ``FFMPEG_BIN`` env override — a bare allowlisted name, or an explicit
       path whose basename is allowlisted and which exists on disk.
    2. Otherwise ``ffmpeg`` via PATH lookup by the OS.
    """
    candidate = os.environ.get("FFMPEG_BIN", "").strip()
    if not candidate:
        return "ffmpeg"
    if "\0" in candidate:
        raise FFmpegError(E_FFMPEG_NOT_FOUND, "ffmpeg path is invalid.")
    basename = os.path.basename(candidate)
    has_separator = os.path.sep in candidate or (
        os.path.altsep is not None and os.path.altsep in candidate
    )
    if has_separator:
        if basename.lower() not in FFMPEG_ALLOWLIST:
            raise FFmpegError(E_FFMPEG_NOT_FOUND, "Unsupported ffmpeg executable name.")
        if not os.path.isfile(candidate):
            raise FFmpegError(E_FFMPEG_NOT_FOUND, "Configured ffmpeg does not exist.")
        return candidate
    if basename.lower() not in FFMPEG_ALLOWLIST:
        raise FFmpegError(E_FFMPEG_NOT_FOUND, "Unsupported ffmpeg command name.")
    return candidate


def validate_input_path(path: str) -> str:
    """Validate a file path used as an ffmpeg input/output.

    Rejects NUL and the shell metacharacters ``; | & \\n`` with a clear error so
    a typo'd path can never be interpreted as a shell command fragment.
    """
    if not isinstance(path, str) or not path.strip():
        raise FFmpegError(E_FFMPEG_FAILED, "File path must not be empty.")
    forbidden = sorted(ch for ch in _FORBIDDEN_IN_PATH if ch in path)
    if forbidden:
        rendered = "".join("\\n" if ch == "\n" else ch for ch in forbidden)
        raise FFmpegError(
            E_FFMPEG_FAILED,
            f"File path contains forbidden characters: {rendered!r}",
        )
    return path


def build_extract_audio_args(
    input_path: str,
    output_path: str,
    *,
    sample_rate: int = DEFAULT_EXTRACT_SAMPLE_RATE,
    channels: int = DEFAULT_EXTRACT_CHANNELS,
    codec: str = DEFAULT_EXTRACT_CODEC,
) -> list[str]:
    """Arguments (excluding the binary) for 16k-mono PCM audio extraction.

    ``ffmpeg -y -nostdin -i <in> -vn -ac <ch> -ar <rate> -c:a <codec>
    -progress pipe:1 -nostats -loglevel error <out>``
    """
    validate_input_path(input_path)
    validate_input_path(output_path)
    return [
        "-y",
        "-nostdin",
        "-i",
        input_path,
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        codec,
        "-progress",
        "pipe:1",
        "-nostats",
        "-loglevel",
        "error",
        output_path,
    ]


def parse_progress_line(line: str) -> dict[str, str] | None:
    """Parse one ``-progress pipe:1`` line (``key=value``) into a dict.

    Returns ``None`` for any non-progress line so callers can stream the whole
    stdout without filtering. Progress keys include ``out_time_us``,
    ``out_time_ms``, ``out_time``, ``speed``, ``progress=continue|end``.
    """
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return {key: value}


def out_time_seconds(parsed: dict[str, str]) -> float | None:
    """Duration processed so far (seconds) from a parsed progress dict."""
    raw = parsed.get("out_time_us")
    if raw is not None:
        try:
            return int(raw) / 1_000_000
        except (TypeError, ValueError):
            return None
    raw = parsed.get("out_time_ms")
    if raw is not None:
        try:
            return int(raw) / 1_000
        except (TypeError, ValueError):
            return None
    return None


def progress_fraction(out_time_us: int, total_seconds: float) -> float:
    """0..1 progress given processed microseconds and a known total duration."""
    if total_seconds <= 0:
        return 0.0
    return max(0.0, min(1.0, (out_time_us / 1_000_000) / total_seconds))


def classify_failure(stderr: str) -> str:
    """User-facing message for a failed ffmpeg run (no secrets / paths)."""
    lowered = (stderr or "").lower()
    if any(marker in lowered for marker in _NO_AUDIO_MARKERS):
        return "The video has no audio track to extract."
    return "FFmpeg failed while processing the video."


def run_ffmpeg(
    args: list[str],
    *,
    cancel: CancellationToken | None = None,
    timeout: float | None = None,
    on_progress: callable | None = None,
    cwd: str | None = None,
) -> FFmpegResult:
    """Run ffmpeg from an argument array, streaming ``-progress pipe:1`` output.

    - ``on_progress`` receives each parsed progress dict (see
      ``parse_progress_line``).
    - ``cwd`` sets the working directory of the ffmpeg subprocess (used by
      render to resolve subtitle files via their bare filename).
    - Cancellation and timeout kill the whole process tree; ``CancelledError`` /
      ``ProcessTimeoutError`` are raised like ``src.core.job.run_process``.
    - ``E_FFMPEG_NOT_FOUND`` is raised when the binary cannot be spawned.
    """
    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("ffmpeg was cancelled before it started")

    if os.name == "nt":
        kwargs: dict = {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
    else:
        kwargs = {"start_new_session": True}

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise FFmpegError(E_FFMPEG_NOT_FOUND, "ffmpeg executable not found.") from exc

    outcome = {"reason": None}
    deadline = time.monotonic() + timeout if timeout is not None else None

    def _reaper() -> None:
        while proc.poll() is None:
            if cancel is not None and cancel.is_cancelled():
                outcome["reason"] = "cancelled"
                _kill_tree(proc, kill_grace=DEFAULT_KILL_GRACE)
                return
            if deadline is not None and time.monotonic() >= deadline:
                outcome["reason"] = "timeout"
                _kill_tree(proc, kill_grace=DEFAULT_KILL_GRACE)
                return
            time.sleep(0.02)

    reaper = threading.Thread(target=_reaper, daemon=True)
    reaper.start()

    out_time_us = 0
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        parsed = parse_progress_line(raw.decode("utf-8", errors="replace"))
        if parsed is None:
            continue
        seconds = out_time_seconds(parsed)
        if seconds is not None:
            out_time_us = int(seconds * 1_000_000)
        if on_progress is not None:
            on_progress(parsed)

    proc.wait(timeout=30)
    reaper.join(timeout=30)

    stderr = b""
    if proc.stderr is not None:
        stderr = proc.stderr.read()
    result = FFmpegResult(
        returncode=proc.returncode or 0,
        stderr=stderr.decode("utf-8", errors="replace"),
        out_time_us=out_time_us,
    )

    if outcome["reason"] == "cancelled":
        raise CancelledError("ffmpeg was cancelled")
    if outcome["reason"] == "timeout":
        raise ProcessTimeoutError(timeout if timeout is not None else 0.0)
    return result
