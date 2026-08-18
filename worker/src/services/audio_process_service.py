"""AudioProcessService — offline audio processing with pure ffmpeg filters.

An honest MVP for the custom-workflow "Audio separation / mix" step: no ML
stem splitter is bundled, so the stage offers real, verifiable ffmpeg modes:

- ``vocal_removal`` — center-channel cancellation for stereo sources (karaoke
  style): content mixed to the center (typically the voice) is cancelled by
  keeping only the side signal, which is what the dubbed voice replaces
  anyway. Mono sources keep the original (nothing to cancel).
- ``normalize`` — EBU R128 loudness normalization (``loudnorm``).
- ``denoise`` — FFT spectral noise reduction (``afftdn``).

The output is a WAV the render maps in place of the video's original audio
(and, when dubbing, mixes the translated voice over instead of the source
speech).
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

E_AUDIO_INVALID = "E_AUDIO_INVALID"

AUDIO_MODES = ("vocal_removal", "normalize", "denoise")

# Center-channel cancellation: keep only the side signal (L-R out of phase),
# which removes mono/centered content (typically the voice). Applied only to
# stereo tracks — mono input is passed through untouched.
_VOCAL_REMOVAL_FILTER = "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0"

_FILTERS = {
    "vocal_removal": _VOCAL_REMOVAL_FILTER,
    "normalize": "loudnorm=I=-16:TP=-1.5:LRA=11",
    "denoise": "afftdn=nf=-25",
}


class AudioError(Exception):
    """Validation/execution failure with a canonical error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AudioProcessParams:
    mode: str


def build_audio_args(input_path: str, output_path: str, mode: str) -> list[str]:
    """Argument array for one audio-processing pass (pure — unit tested)."""
    if mode not in _FILTERS:
        raise AudioError(E_AUDIO_INVALID, f"unknown audio mode: {mode!r}")
    return [
        "-y",
        "-nostdin",
        "-i",
        input_path,
        "-vn",
        "-af",
        _FILTERS[mode],
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        output_path,
    ]


def process_audio(
    input_path: str,
    output_path: str,
    mode: str,
    *,
    cancel: CancellationToken | None = None,
    on_progress: callable | None = None,
) -> str:
    """Run one processing pass. Returns ``output_path`` on success."""
    if not os.path.isfile(input_path):
        raise AudioError(E_AUDIO_INVALID, "input video/audio does not exist")
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    duration = 0.0
    try:
        meta = probe(input_path)
        duration = float(meta.duration or 0.0)
    except MediaProbeError:
        duration = 0.0

    args = build_audio_args(input_path, output_path, mode)
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
        raise AudioError(
            E_FFMPEG_FAILED,
            f"ffmpeg audio processing failed (rc={result.returncode})",
        )
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise AudioError(E_AUDIO_INVALID, "audio processing produced no output file")
    logger.info("audio %s done in %.1fs", mode, time.monotonic() - started)
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
