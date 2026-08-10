"""Safe whisper.cpp runner (TASK-015) — CPU/AMD/Intel fallback engine.

whisper-cli is a standalone process (never bundled in the repo; built via
``worker/scripts/build_whisper_cpp.ps1`` and placed under ``vendor/``), so this
module mirrors the FFmpeg discipline of TASK-012:

- **Argument-array only** — the binary is launched with a list of args, never a
  shell string.
- **Executable allowlist** — ``WHISPER_CPP_BIN`` may only name ``whisper-cli``
  / ``whisper-cli.exe``.
- **Cancellation / timeout** — process-tree kill via ``src.core.job._kill_tree``,
  same semantics as ``run_ffmpeg``.

The three mandatory mitigations from MASTER_PLAN §14.2 / TASK-015:

1. **beam_size <= 6** — ``clamp_beam_size`` caps the beam (AMD Radeon 780M
   segfault workaround, issue #3723).
2. **--no-flash-attn on AMD/Intel Vulkan** — ``no_flash_attn=True`` from the
   strategy (AMD RDNA4 crash workaround, issue #3806).
3. **Single-threaded init** — ``stt_service`` serializes model spawns behind a
   lock; see ``stt_service._WHISPER_INIT_LOCK`` (issue #3638). Vulkan init
   failure is non-fatal at the app level: the service falls back to CPU with a
   logged, user-visible warning (Vulkan is a compatibility enhancement, not a
   blocker).

Progress: ``--progress`` prints ``whisper_print_progress_callback: progress =
NN%`` on stderr; parsed into a 0..1 ratio and streamed to ``on_progress``.

Error taxonomy: ``E_WHISPER_CPP_NOT_FOUND`` (binary missing) and
``E_WHISPER_CPP_FAILED`` (any failure / bad output).
"""

from __future__ import annotations

import json
import logging
import os
import re
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

E_WHISPER_CPP_NOT_FOUND = "E_WHISPER_CPP_NOT_FOUND"
E_WHISPER_CPP_FAILED = "E_WHISPER_CPP_FAILED"

#: Mitigation 1 (MASTER_PLAN §14.2): beam > 6 can segfault AMD Radeon 780M.
MAX_BEAM_SIZE = 6

#: Allowlisted executable names (bare PATH lookup or explicit path basename).
WHISPER_CPP_ALLOWLIST = frozenset({"whisper-cli", "whisper-cli.exe"})

_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d{1,3})\s*%")


class WhisperCppError(Exception):
    """whisper.cpp failure carrying the architecture error code (§28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class WhisperCppResult:
    """Outcome of a finished whisper-cli run."""

    returncode: int
    output_json: str
    progress: float | None = None


def resolve_whisper_cli() -> str:
    """Resolve the whisper-cli executable through the allowlist.

    1. ``WHISPER_CPP_BIN`` env override — a bare allowlisted name, or an
       explicit path whose basename is allowlisted and which exists on disk.
    2. Otherwise ``whisper-cli`` via PATH lookup by the OS.
    """
    candidate = os.environ.get("WHISPER_CPP_BIN", "").strip()
    if not candidate:
        return "whisper-cli"
    if "\0" in candidate:
        raise WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "whisper-cli path is invalid.")
    basename = os.path.basename(candidate)
    has_separator = os.path.sep in candidate or (
        os.path.altsep is not None and os.path.altsep in candidate
    )
    if has_separator:
        if basename.lower() not in WHISPER_CPP_ALLOWLIST:
            raise WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "Unsupported whisper-cli executable name.")
        if not os.path.isfile(candidate):
            raise WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "Configured whisper-cli does not exist.")
        return candidate
    if basename.lower() not in WHISPER_CPP_ALLOWLIST:
        raise WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "Unsupported whisper-cli command name.")
    return candidate


def clamp_beam_size(beam_size: int | None) -> int:
    """Mitigation 1: cap the beam at ``MAX_BEAM_SIZE`` (default 5)."""
    if beam_size is None:
        return 5
    return max(1, min(int(beam_size), MAX_BEAM_SIZE))


def build_transcribe_args(
    model_path: str,
    audio_path: str,
    *,
    language: str | None = None,
    num_threads: int | None = None,
    beam_size: int | None = 5,
    no_flash_attn: bool = False,
) -> list[str]:
    """Arguments (excluding the binary) for one whisper-cli transcription.

    ``-m <model> -f <audio> [-l <lang>] [-t <threads>] --beam-size <b>
    [--no-flash-attn] --output-json``

    ``--output-json`` emits the full transcript JSON on stdout.
    """
    args = ["-m", model_path, "-f", audio_path]
    if language:
        args += ["-l", language]
    if num_threads is not None and num_threads > 0:
        args += ["-t", str(int(num_threads))]
    args += ["--beam-size", str(clamp_beam_size(beam_size))]
    if no_flash_attn:
        args += ["--no-flash-attn"]
    args += ["--output-json"]
    return args


def parse_progress_percent(line: str) -> float | None:
    """Extract a ``progress = NN%`` ratio (0..1) from a whisper-cli stderr line."""
    match = _PROGRESS_RE.search(line or "")
    if match is None:
        return None
    try:
        pct = float(match.group(1))
    except ValueError:
        return None
    return max(0.0, min(1.0, pct / 100.0))


def _parse_timestamp(value: str | None) -> float | None:
    """Parse whisper.cpp timestamps like ``00:00:05,620`` or ``00:00:05.620``."""
    if not value:
        return None
    cleaned = value.strip().replace(",", ".")
    try:
        parts = cleaned.split(":")
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_json_output(text: str) -> dict:
    """Parse whisper-cli ``--output-json`` into transcript-ready segments.

    Returns ``{"segments": [{text, start, end}, ...], "language": str|None}``.
    Prefers the integer millisecond ``offsets``, falls back to ``timestamps``
    strings. Empty segments are dropped. Raises ``E_WHISPER_CPP_FAILED`` on
    malformed output so callers never leak a raw JSON trace.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WhisperCppError(E_WHISPER_CPP_FAILED, "whisper-cli returned invalid JSON output.") from exc

    transcription = data.get("transcription")
    if transcription is None:
        raise WhisperCppError(E_WHISPER_CPP_FAILED, "whisper-cli output has no transcription block.")

    segments: list[dict] = []
    for item in transcription:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        offsets = item.get("offsets") or {}
        start_ms = offsets.get("from")
        end_ms = offsets.get("to")
        if start_ms is not None and end_ms is not None:
            start = float(start_ms) / 1000.0
            end = float(end_ms) / 1000.0
        else:
            timestamps = item.get("timestamps") or {}
            start = _parse_timestamp(timestamps.get("from"))
            end = _parse_timestamp(timestamps.get("to"))
            if start is None or end is None:
                continue
        segments.append(
            {
                "text": text,
                "start": max(0.0, start),
                "end": max(0.0, end if end >= start else start),
            }
        )
    return {"segments": segments, "language": data.get("language")}


def run_whisper_cli(
    args: list[str],
    *,
    cancel: CancellationToken | None = None,
    timeout: float | None = None,
    on_progress: callable | None = None,
) -> WhisperCppResult:
    """Run whisper-cli from an argument array, streaming ``--progress``.

    - ``on_progress(ratio)`` receives the last parsed progress percent.
    - Cancellation and timeout kill the whole process tree; ``CancelledError`` /
      ``ProcessTimeoutError`` are raised like ``src.core.job.run_process``.
    - ``E_WHISPER_CPP_NOT_FOUND`` is raised when the binary cannot be spawned.
    """
    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("whisper-cli was cancelled before it started")

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
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise WhisperCppError(E_WHISPER_CPP_NOT_FOUND, "whisper-cli executable not found.") from exc

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

    progress: float | None = None
    assert proc.stderr is not None
    for raw in iter(proc.stderr.readline, b""):
        line = raw.decode("utf-8", errors="replace")
        ratio = parse_progress_percent(line)
        if ratio is not None:
            progress = ratio
            if on_progress is not None:
                on_progress(ratio)

    proc.wait(timeout=30)
    reaper.join(timeout=30)

    stdout = b""
    if proc.stdout is not None:
        stdout = proc.stdout.read()
    result = WhisperCppResult(
        returncode=proc.returncode or 0,
        output_json=stdout.decode("utf-8", errors="replace"),
        progress=progress,
    )

    if outcome["reason"] == "cancelled":
        raise CancelledError("whisper-cli was cancelled")
    if outcome["reason"] == "timeout":
        raise ProcessTimeoutError(timeout if timeout is not None else 0.0)
    return result
