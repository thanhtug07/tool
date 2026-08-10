"""MediaProbeService (TASK-009): ``ffprobe`` -> canonical MediaMetadata.

Runs ``ffprobe -print_format json -show_format -show_streams`` as a subprocess
using an **argument array only** (never a shell command string), parses the
JSON into the canonical `MediaMetadata` contract (`schemas/media.schema.json`,
TASK-007 single-source-of-truth), normalizes rotation and FPS to rationals, and
maps failures onto the architecture error taxonomy (MASTER_PLAN §28.1):

- ``E_VIDEO_INVALID``   — file is not a video / cannot be opened / no video stream
- ``E_VIDEO_CORRUPTED`` — recognized container but damaged / decode failure
- ``E_FFMPEG_NOT_FOUND``— ffprobe binary unavailable

Security model
--------------
- The video path is passed as a *subprocess argument*, never interpolated into
  a shell string, so shell metacharacters in a filename are inert. The only
  rejected character is NUL, which the OS layer cannot carry anyway.
- The ffprobe executable is resolved through an **allowlist** (`FFPROBE_BIN` may
  only name ``ffprobe``/``ffprobe.exe``, bare or as an existing path whose
  basename matches) — it can never point at an arbitrary executable.
- Error messages never embed raw command lines or the video path.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from typing import Any

from src.api.schemas import AudioStream, MediaMetadata, Rational, SubtitleStream, VideoStream

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 30.0

E_FFMPEG_NOT_FOUND = "E_FFMPEG_NOT_FOUND"
E_VIDEO_INVALID = "E_VIDEO_INVALID"
E_VIDEO_CORRUPTED = "E_VIDEO_CORRUPTED"

# Allowlisted executable names (bare PATH lookup or explicit path basename).
FFPROBE_ALLOWLIST = frozenset({"ffprobe", "ffprobe.exe"})

# ffprobe side-data types that can carry the rotation value.
_ROTATION_SIDE_DATA_TYPES = frozenset({"Display Matrix", "Spherical Mapping"})

_DEFAULT_TIMEOUT_MESSAGE = "ffprobe timed out while probing the video."


class MediaProbeError(Exception):
    """Probe failure carrying the architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _ProbeResult:
    """Minimal subprocess outcome (mocked easily in tests)."""

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def resolve_ffprobe() -> str:
    """Resolve the ffprobe executable through the allowlist.

    1. ``FFPROBE_BIN`` env override — a bare allowlisted name, or an explicit
       path whose basename is allowlisted and which exists on disk.
    2. Otherwise ``ffprobe`` via PATH lookup by the OS.
    """
    candidate = os.environ.get("FFPROBE_BIN", "").strip()
    if not candidate:
        return "ffprobe"
    return _validate_ffprobe_candidate(candidate)


def _validate_ffprobe_candidate(candidate: str) -> str:
    if "\0" in candidate:
        raise MediaProbeError(E_FFMPEG_NOT_FOUND, "ffprobe path is invalid.")
    basename = os.path.basename(candidate)
    has_separator = os.path.sep in candidate or (
        os.path.altsep is not None and os.path.altsep in candidate
    )
    if has_separator:
        if basename.lower() not in FFPROBE_ALLOWLIST:
            raise MediaProbeError(
                E_FFMPEG_NOT_FOUND, "Unsupported ffprobe executable name."
            )
        if not os.path.isfile(candidate):
            raise MediaProbeError(E_FFMPEG_NOT_FOUND, "Configured ffprobe does not exist.")
        return candidate
    if basename.lower() not in FFPROBE_ALLOWLIST:
        raise MediaProbeError(E_FFMPEG_NOT_FOUND, "Unsupported ffprobe command name.")
    return candidate


def probe(path: str) -> MediaMetadata:
    """Probe ``path`` and return canonical MediaMetadata.

    Raises ``MediaProbeError`` with an architecture error code on any failure.
    Lightly damaged files are re-probed with ``-err_detect ignore_err`` so
    metadata can still be recovered (TASK-009 implementation note).
    """
    video_path = _validate_input_path(path)
    ffprobe = resolve_ffprobe()

    result = _run_ffprobe(ffprobe, video_path, [])
    if result.returncode == 0:
        return parse_ffprobe_output(result.stdout)

    # Retry leniently for lightly corrupted files.
    result = _run_ffprobe(ffprobe, video_path, ["-err_detect", "ignore_err"])
    if result.returncode == 0:
        return parse_ffprobe_output(result.stdout)

    raise _classify_failure(result, video_path)


def parse_ffprobe_output(stdout: bytes | str) -> MediaMetadata:
    """Build MediaMetadata from the raw ``ffprobe -show_format -show_streams`` JSON.

    Separated from the subprocess so it can be unit-tested without ffprobe.
    """
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MediaProbeError(E_VIDEO_INVALID, "ffprobe returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise MediaProbeError(E_VIDEO_INVALID, "ffprobe returned an unexpected payload.")
    return _build_metadata(data)


# --------------------------------------------------------------------------
# ffprobe JSON -> MediaMetadata
# --------------------------------------------------------------------------


def _build_metadata(data: dict[str, Any]) -> MediaMetadata:
    streams = data.get("streams") or []
    container = data.get("format") or {}
    if not isinstance(streams, list) or not isinstance(container, dict):
        raise MediaProbeError(E_VIDEO_INVALID, "ffprobe output has an unexpected shape.")

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaProbeError(E_VIDEO_INVALID, "No video stream found in the file.")

    primary = _video_stream(video)
    return MediaMetadata(
        schema_version=1,
        duration=_container_duration(container, primary),
        width=primary.width,
        height=primary.height,
        fps=primary.fps,
        codec=primary.codec,
        bitrate=_as_int(container.get("bit_rate")),
        rotation=_stream_rotation(video),
        format=(container.get("format_name") or "unknown"),
        aspect_ratio=primary.aspect_ratio,
        video_streams=[_video_stream(s) for s in streams if s.get("codec_type") == "video"],
        audio_streams=[_audio_stream(s) for s in streams if s.get("codec_type") == "audio"],
        subtitle_streams=[
            _subtitle_stream(s) for s in streams if s.get("codec_type") == "subtitle"
        ],
    )


def _video_stream(stream: dict[str, Any]) -> VideoStream:
    width = _as_int(stream.get("width"))
    height = _as_int(stream.get("height"))
    return VideoStream(
        index=_as_int(stream.get("index")) or 0,
        codec=stream.get("codec_name"),
        profile=stream.get("profile"),
        width=width,
        height=height,
        fps=_stream_fps(stream),
        pixel_format=stream.get("pix_fmt"),
        aspect_ratio=_aspect_ratio(stream, width, height),
        bitrate=_as_int(stream.get("bit_rate")),
        duration=_as_float(stream.get("duration")),
    )


def _audio_stream(stream: dict[str, Any]) -> AudioStream:
    tags = stream.get("tags") or {}
    return AudioStream(
        index=_as_int(stream.get("index")) or 0,
        codec=stream.get("codec_name"),
        channels=_as_int(stream.get("channels")),
        sample_rate=_as_int(stream.get("sample_rate")),
        bitrate=_as_int(stream.get("bit_rate")),
        duration=_as_float(stream.get("duration")),
        language=tags.get("language") if isinstance(tags, dict) else None,
    )


def _subtitle_stream(stream: dict[str, Any]) -> SubtitleStream:
    tags = stream.get("tags") or {}
    return SubtitleStream(
        index=_as_int(stream.get("index")) or 0,
        codec=stream.get("codec_name"),
        language=tags.get("language") if isinstance(tags, dict) else None,
        title=tags.get("title") if isinstance(tags, dict) else None,
        duration=_as_float(stream.get("duration")),
    )


def _stream_fps(stream: dict[str, Any]) -> Rational:
    """Frame rate as an exact rational; prefers ``avg_frame_rate``."""
    fps = _parse_rational(stream.get("avg_frame_rate"))
    if fps is not None and fps.numerator > 0:
        return fps
    fps = _parse_rational(stream.get("r_frame_rate"))
    if fps is not None and fps.numerator > 0:
        return fps
    return Rational(numerator=0, denominator=1)


def _stream_rotation(stream: dict[str, Any]) -> int:
    """Effective rotation in degrees, normalized to {0, 90, 180, 270}.

    Handles both metadata sources, which use opposite conventions:

    - ``tags.rotate`` (legacy MP4) is *clockwise*: a CCW-90 file reports ``270``
      (ffmpeg converts the tag to a display matrix via ``-rotate_override``).
    - Display Matrix ``side_data`` (ffmpeg >= 4) is *counter-clockwise* in
      ``[-180, 180]`` (``av_display_rotation_get``); ``-90`` means ``270``.

    Both are normalized to the counter-clockwise angle for upright display.
    """
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            tag_value = int(tags["rotate"]) % 360
        except (TypeError, ValueError):
            return 0
        degrees = (360 - tag_value) % 360
    else:
        raw: Any = None
        for side_data in stream.get("side_data_list") or []:
            if (
                isinstance(side_data, dict)
                and side_data.get("side_data_type") in _ROTATION_SIDE_DATA_TYPES
                and "rotation" in side_data
            ):
                raw = side_data["rotation"]
                break
        if raw is None:
            return 0
        try:
            degrees = int(raw) % 360
        except (TypeError, ValueError):
            return 0
    candidates = (0, 90, 180, 270)
    return min(candidates, key=lambda c: min(abs(degrees - c), 360 - abs(degrees - c)))


def _parse_rational(value: object) -> Rational | None:
    """Parse an ffprobe ``num/den`` frame-rate string into a Rational."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        num, den = value.split("/", 1)
        numerator, denominator = int(num), int(den)
    except (ValueError, AttributeError):
        return None
    if denominator <= 0 or numerator < 0:
        return None
    return Rational(numerator=numerator, denominator=denominator)


def _aspect_ratio(stream: dict[str, Any], width: int | None, height: int | None) -> str | None:
    """Prefer the display aspect ratio; fall back to a gcd of the dimensions."""
    dar = stream.get("display_aspect_ratio")
    if isinstance(dar, str) and dar.strip():
        return dar
    if width and height:
        divisor = math.gcd(width, height)
        if divisor > 0:
            return f"{width // divisor}:{height // divisor}"
    return None


def _container_duration(container: dict[str, Any], primary: VideoStream) -> float:
    duration = _as_float(container.get("duration"))
    if duration is not None:
        return duration
    return primary.duration or 0.0


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Input validation / subprocess / failure mapping
# --------------------------------------------------------------------------


def _validate_input_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise MediaProbeError(E_VIDEO_INVALID, "Video path must not be empty.")
    if "\0" in path:
        raise MediaProbeError(E_VIDEO_INVALID, "Video path contains a NUL byte.")
    if not os.path.isfile(path):
        raise MediaProbeError(E_VIDEO_INVALID, "Video file does not exist.")
    return path


def _run_ffprobe(ffprobe: str, path: str, extra_args: list[str]) -> _ProbeResult:
    """Run ffprobe with an argument array; never a shell string."""
    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        *extra_args,
        path,
    ]
    logger.debug("running ffprobe with %d arguments (path redacted)", len(command))
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaProbeError(E_FFMPEG_NOT_FOUND, "ffprobe executable not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(E_VIDEO_CORRUPTED, _DEFAULT_TIMEOUT_MESSAGE) from exc
    return _ProbeResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _classify_failure(result: _ProbeResult, path: str) -> MediaProbeError:
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
    logger.warning(
        "ffprobe failed (exit=%s): %s",
        result.returncode,
        _sanitize_stderr(stderr, path),
    )
    lowered = stderr.lower()
    if _is_mp4_container(path):
        return MediaProbeError(E_VIDEO_CORRUPTED, "Video container is damaged.")
    if any(marker in lowered for marker in ("invalid data found", "does not look like", "no such file")):
        return MediaProbeError(E_VIDEO_INVALID, "The file is not a valid video.")
    return MediaProbeError(E_VIDEO_CORRUPTED, "Video is corrupted and cannot be read.")


def _is_mp4_container(path: str) -> bool:
    """A file whose header carries the MP4/MOV ``ftyp`` box is a recognized
    container, so a probe failure means it is damaged rather than 'not a video'."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    return len(header) >= 8 and header[4:8] == b"ftyp"


def _sanitize_stderr(stderr: str, path: str) -> str:
    """Strip the video path (and any surrounding context) from log lines."""
    if path:
        stderr = stderr.replace(path, "<video>")
    return stderr.strip()[:500]
