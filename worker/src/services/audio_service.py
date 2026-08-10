"""AudioService (TASK-012): extract a 16k-mono WAV from a video with FFmpeg.

Wraps the safe ffmpeg runner (`src.core.ffmpeg`) with the canonical extract spec
(MASTER_PLAN §13 / ARCHITECTURE_DECISION.md §3.7): ``-vn -ac 1 -ar 16000 -c:a
pcm_s16le``, streaming progress via ``-progress pipe:1`` and cancellation by
killing the process tree. The cache key ``audio:{sha256(video)}:{spec}`` is
built with ``src.services.cache.audio_key`` (content-addressed, FROZEN §3.7).

Errors (MASTER_PLAN §28.1):

- ``E_FFMPEG_NOT_FOUND`` — ffmpeg binary unavailable.
- ``E_FFMPEG_FAILED``   — ffmpeg failed; the message distinguishes a missing
  audio track from a generic failure.

Security model: paths are validated by ``validate_input_path`` (no NUL, no
``; | & \\n``) and passed as *arguments*, never interpolated into a shell
string. Error messages never embed the video path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

from src.core.ffmpeg import (
    E_FFMPEG_FAILED,
    FFmpegError,
    build_extract_audio_args,
    classify_failure,
    out_time_seconds,
    progress_fraction,
    resolve_ffmpeg,
    run_ffmpeg,
    validate_input_path,
)
from src.core.job import CancellationToken

logger = logging.getLogger(__name__)

#: Progress callback: ``(fraction 0..1)`` of the extraction.
ProgressCallback = Callable[[float], None]


@dataclass(frozen=True)
class AudioExtractResult:
    """Outcome of a successful extraction."""

    output_path: str
    file_size_bytes: int
    duration_seconds: float | None


def extract_audio(
    video_path: str,
    output_path: str,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    codec: str = "pcm_s16le",
    total_duration_seconds: float | None = None,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
    ffmpeg_bin: str | None = None,
) -> AudioExtractResult:
    """Extract ``codec`` audio from ``video_path`` into ``output_path``.

    - ``on_progress(fraction)`` is called as ffmpeg reports progress; it needs
      ``total_duration_seconds`` (e.g. from MediaProbeService) to map raw time
      to a 0..1 ratio.
    - ``cancel`` aborts by killing the ffmpeg process tree and raises
      ``CancelledError``.
    """
    input_path = validate_input_path(video_path)
    if not os.path.isfile(input_path):
        raise FFmpegError(E_FFMPEG_FAILED, "Video file does not exist.")
    validate_input_path(output_path)
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    binary = ffmpeg_bin or resolve_ffmpeg()
    args = [binary] + build_extract_audio_args(
        input_path,
        output_path,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
    )
    logger.debug("running ffmpeg with %d arguments (paths redacted)", len(args))

    def _on_line(parsed: dict[str, str]) -> None:
        if on_progress is None or total_duration_seconds is None:
            return
        seconds = out_time_seconds(parsed)
        if seconds is not None:
            on_progress(progress_fraction(int(seconds * 1_000_000), total_duration_seconds))

    result = run_ffmpeg(args, cancel=cancel, on_progress=_on_line)
    if result.returncode != 0:
        raise FFmpegError(E_FFMPEG_FAILED, classify_failure(result.stderr))
    if not os.path.isfile(output_path):
        raise FFmpegError(E_FFMPEG_FAILED, "FFmpeg reported success but produced no audio file.")

    size = os.path.getsize(output_path)
    return AudioExtractResult(
        output_path=output_path,
        file_size_bytes=size,
        duration_seconds=result.duration_seconds if result.out_time_us > 0 else None,
    )
