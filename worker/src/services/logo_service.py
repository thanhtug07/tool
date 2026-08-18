"""LogoService — ffmpeg ``delogo`` burn-out of a user-marked region.

Removes a watermark/logo by interpolating over the marked rectangle with
libavfilter's ``delogo`` (T. delogo, built into the bundled ffmpeg). The
region is supplied in source-pixel coordinates; an optional time window limits
the removal to a ``[time_start, time_end]`` span (the logo may only appear in
part of the video).

The output is a fresh MP4 (libx264 + original audio copied) so the pipeline's
render stage can burn subtitles on top of the logo-free video.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from src.core.ffmpeg import E_FFMPEG_FAILED, resolve_ffmpeg, run_ffmpeg
from src.core.job import CancellationToken
from src.services.media_service import MediaProbeError, probe

logger = logging.getLogger(__name__)

E_LOGO_INVALID = "E_LOGO_INVALID"


class LogoError(Exception):
    """Validation/execution failure with a canonical error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LogoRegion:
    """Marked logo rectangle (source-pixel coords) + optional time window."""

    x: int
    y: int
    width: int
    height: int
    time_start: float | None = None
    time_end: float | None = None


def build_logo_args(input_path: str, output_path: str, region: LogoRegion) -> list[str]:
    """Argument array for one delogo pass (pure — unit tested)."""
    if region.width <= 0 or region.height <= 0 or region.x < 0 or region.y < 0:
        raise LogoError(E_LOGO_INVALID, "logo region must have positive size and non-negative origin")
    expr = f"delogo=x={region.x}:y={region.y}:w={region.width}:h={region.height}"
    if region.time_start is not None or region.time_end is not None:
        start = region.time_start if region.time_start is not None else 0
        end = region.time_end if region.time_end is not None else 1 << 30
        if end <= start:
            raise LogoError(E_LOGO_INVALID, "logo time window end must be after start")
        expr += f":enable='between(t,{start},{end})'"
    return [
        "-y",
        "-nostdin",
        "-i",
        input_path,
        "-vf",
        expr,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "copy",
        output_path,
    ]


def clamp_logo_region(region: LogoRegion, width: int, height: int) -> LogoRegion:
    """Fit a logo region strictly inside the frame (pure — unit tested).

    ffmpeg ``delogo`` rejects regions touching the frame edge, so the origin
    is clamped to keep a 1 px margin. Regions that cannot fit the frame at
    all raise ``E_LOGO_INVALID``.
    """
    if width <= 0 or height <= 0:
        raise LogoError(E_LOGO_INVALID, "cannot clamp logo region without frame size")
    max_x = width - region.width - 1
    max_y = height - region.height - 1
    if max_x < 1 or max_y < 1:
        raise LogoError(E_LOGO_INVALID, "logo region must fit inside the video frame")
    x = min(max(1, region.x), max_x)
    y = min(max(1, region.y), max_y)
    return LogoRegion(
        x=x,
        y=y,
        width=region.width,
        height=region.height,
        time_start=region.time_start,
        time_end=region.time_end,
    )


def remove_logo(
    input_path: str,
    output_path: str,
    region: LogoRegion,
    *,
    cancel: CancellationToken | None = None,
    on_progress: callable | None = None,
) -> str:
    """Run the delogo pass. Returns ``output_path`` on success."""
    if not os.path.isfile(input_path):
        raise LogoError(E_LOGO_INVALID, "input video does not exist")
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    duration = 0.0
    width = height = 0
    try:
        meta = probe(input_path)
        duration = float(meta.duration or 0.0)
        width = int(meta.width or 0)
        height = int(meta.height or 0)
    except MediaProbeError:
        pass

    if width and height:
        region = clamp_logo_region(region, width, height)

    args = build_logo_args(input_path, output_path, region)
    started = time.monotonic()

    def _progress(parsed: dict[str, str]) -> None:
        if on_progress is None or duration <= 0:
            return
        seconds = _out_time_seconds(parsed)
        if seconds is None:
            return
        on_progress(min(1.0, seconds / duration))

    result = run_ffmpeg(
        [resolve_ffmpeg(), *args],
        cancel=cancel,
        on_progress=_progress if on_progress is not None else None,
    )
    if result.returncode != 0:
        raise LogoError(E_FFMPEG_FAILED, f"ffmpeg delogo failed (rc={result.returncode})")
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise LogoError(E_LOGO_INVALID, "logo removal produced no output file")
    logger.info("logo removal done in %.1fs", time.monotonic() - started)
    return output_path


def _out_time_seconds(parsed: dict[str, str]) -> float | None:
    """Parse ``out_time_us``/``out_time`` from a ``-progress pipe:1`` dict."""
    us = parsed.get("out_time_us")
    if us is not None:
        try:
            return int(us) / 1_000_000
        except (TypeError, ValueError):
            return None
    raw = parsed.get("out_time")
    if not raw:
        return None
    try:
        h, m, s = raw.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (TypeError, ValueError):
        return None
