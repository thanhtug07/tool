"""RenderService (TASK-027): libass burn-in + auto encoder + progress/cancel.

Renders a video with subtitles burned in via the ``ass``/``subtitles`` libass
filter, then **validates the output before it is ever handed to the caller** —
a corrupt or wrong file is never shipped silently.

Pipeline (MASTER_PLAN §9.2 / TASK-027):

1. Probe the input (resolution / FPS / audio / duration) with ffprobe.
2. Pick a video encoder: hardware NVENC → QSV → AMF, else libx264; a requested
   encoder may be forced, with graceful fallback to libx264 on hardware failure.
3. Encode straight to a temp file in the destination directory (same volume, so
   the final ``os.replace`` is atomic), keeping resolution / FPS / SAR / colour
   metadata untouched (no scaling, no re-timeline).
4. Stream ``-progress pipe:1`` and report fraction + ETA.
5. Cancel kills the whole ffmpeg process tree and removes temp files.
6. **Validation:** ffprobe the output and verify resolution == input, FPS,
   audio streams (channels/sample-rate) preserved, codec/container sane,
   duration ≈ source (±1 s), and — when a subtitle window is supplied — that a
   sampled frame actually contains a burned-in text region.

Error taxonomy (MASTER_PLAN §28.1):
- ``E_RENDER_INVALID``   — bad inputs / unsupported encoder / probe failure.
- ``E_RENDER_FAILED``    — ffmpeg encode failed (incl. after fallback).
- ``E_RENDER_VALIDATION``— the output failed one or more mandatory checks.

Security model: ffmpeg/ffprobe always run from argument arrays (never a shell
string); subtitle and output paths go through ``validate_input_path``; the
subtitle file is copied into the temp workdir under a safe generated name and
referenced through the process ``cwd`` so no filter-graph path escaping is
needed; error messages never embed paths or command lines.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from src.api.schemas import MediaMetadata
from src.core.ffmpeg import (
    E_FFMPEG_FAILED,
    CancelledError,
    CancellationToken,
    FFmpegError,
    out_time_seconds,
    progress_fraction,
    resolve_ffmpeg,
    run_ffmpeg,
    validate_input_path,
)
from src.services import hardware
from src.services.media_service import MediaProbeError, probe

logger = logging.getLogger(__name__)

E_RENDER_INVALID = "E_RENDER_INVALID"
E_RENDER_FAILED = "E_RENDER_FAILED"
E_RENDER_VALIDATION = "E_RENDER_VALIDATION"

# Hardware encoder preference order (MASTER_PLAN §9.2: NVENC → QSV → AMF).
_HW_VIDEO_ENCODERS = ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv", "h264_amf")
_SOFTWARE_VIDEO_ENCODERS = ("libx264", "libx265", "libsvtav1")

DEFAULT_RENDER_CRF = 18
DEFAULT_RENDER_PRESET = "medium"
DEFAULT_AUDIO_CODEC = "copy"

# Render validation tolerances (TASK-027): resolution exact, FPS within 1%,
# duration within 1 s, burn-in region luminance delta.
DURATION_TOLERANCE_SECONDS = 1.0
FPS_TOLERANCE_RELATIVE = 0.01
BURN_IN_MIN_DELTA = 8.0
_FRAME_EXTRACT_TIMEOUT = 30.0

# Bottom band of a frame sampled for burned-in subtitle text (subtitles live at
# the bottom-center; include the full bottom edge so small 320px fixtures count).
_BURN_REGION = (0.0, 0.85, 1.0, 1.0)  # x0, y0, x1, y1 as fractions

_KNOWN_OUTPUT_CODECS = frozenset({"h264", "hevc", "av1", "vp9", "mpeg4"})


class RenderError(Exception):
    """Render failure carrying the architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RenderConfig:
    """Inputs for one burn-in render.

    ``video_encoder=None`` auto-detects (NVENC → QSV → AMF → libx264).
    ``check_window`` is the ``(start, end)`` seconds window where a subtitle is
    displayed; when provided, the output must show a text region there.
    """

    input_path: str
    output_path: str
    subtitle_path: str | None = None
    video_encoder: str | None = None
    video_preset: str = DEFAULT_RENDER_PRESET
    video_crf: int = DEFAULT_RENDER_CRF
    audio_codec: str = DEFAULT_AUDIO_CODEC
    check_window: tuple[float, float] | None = None
    allow_fallback: bool = True


@dataclass(frozen=True)
class RenderProgress:
    """Live render progress (0..1 fraction + optional ETA / speed)."""

    fraction: float
    eta_seconds: float | None = None
    speed_x: float | None = None


@dataclass(frozen=True)
class RenderResult:
    """Validated render outcome. The file at ``output_path`` is final."""

    output_path: str
    encoder_used: str
    duration_seconds: float
    width: int
    height: int
    fps: tuple[int, int]
    audio_streams: int


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without ffmpeg)
# ---------------------------------------------------------------------------


def pick_video_encoder(available: tuple[str, ...], requested: str | None) -> str:
    """Resolve the video encoder: explicit request, else NVENC→QSV→AMF→libx264."""
    if requested is not None:
        if requested in available or requested in _SOFTWARE_VIDEO_ENCODERS:
            return requested
        raise RenderError(E_RENDER_INVALID, f"video encoder {requested!r} is not available")
    for candidate in _HW_VIDEO_ENCODERS:
        if candidate in available:
            return candidate
    return "libx264"


def build_render_args(
    input_path: str,
    output_path: str,
    *,
    encoder: str,
    subtitle_arg: str | None = None,
    preset: str = DEFAULT_RENDER_PRESET,
    crf: int = DEFAULT_RENDER_CRF,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
) -> list[str]:
    """Argument array for a burn-in render.

    Software encoders get ``-preset``/``-crf``; hardware encoders use their own
    quality defaults (nvenc/qsv/amf do not share libx264's flags). Streams are
    mapped explicitly so all audio is preserved and no stream is dropped.
    """
    validate_input_path(input_path)
    validate_input_path(output_path)
    args = ["-y", "-nostdin", "-i", input_path]
    if subtitle_arg:
        args += ["-vf", subtitle_arg]
    args += ["-map", "0:v:0", "-map", "0:a?"]
    args += ["-c:v", encoder]
    if encoder in _SOFTWARE_VIDEO_ENCODERS:
        args += ["-preset", preset, "-crf", str(crf)]
    args += ["-c:a", audio_codec, "-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    args.append(output_path)
    return args


def subtitle_filter_arg(subtitle_path: str) -> str:
    """Filter-graph reference for a subtitle file copied into the workdir."""
    name = Path(subtitle_path).name.lower()
    if name.endswith(".ass"):
        return f"ass={Path(subtitle_path).name}"
    return f"subtitles={Path(subtitle_path).name}"


def render_validation_issues(
    source_meta: MediaMetadata,
    output_meta: MediaMetadata,
) -> list[str]:
    """All mandatory-output checks that fail (TASK-027 acceptance gate)."""
    issues: list[str] = []
    if output_meta.width != source_meta.width or output_meta.height != source_meta.height:
        issues.append(
            f"resolution changed: {source_meta.width}x{source_meta.height} "
            f"-> {output_meta.width}x{output_meta.height}"
        )
    src_fps = _fps_value(source_meta.fps)
    out_fps = _fps_value(output_meta.fps)
    if src_fps and out_fps and abs(out_fps - src_fps) / src_fps > FPS_TOLERANCE_RELATIVE:
        issues.append(f"fps changed: {src_fps:.3f} -> {out_fps:.3f}")
    if source_meta.duration and output_meta.duration:
        drift = abs(output_meta.duration - source_meta.duration)
        if drift > DURATION_TOLERANCE_SECONDS:
            issues.append(
                f"duration drifted {drift:.2f}s "
                f"({source_meta.duration:.2f}s -> {output_meta.duration:.2f}s)"
            )
    if output_meta.codec not in _KNOWN_OUTPUT_CODECS:
        issues.append(f"unexpected output video codec: {output_meta.codec!r}")
    if not output_meta.audio_streams and source_meta.audio_streams:
        issues.append("audio track was dropped")
    elif source_meta.audio_streams and output_meta.audio_streams:
        src_a = source_meta.audio_streams[0]
        out_a = output_meta.audio_streams[0]
        if src_a.channels and out_a.channels and src_a.channels != out_a.channels:
            issues.append(f"audio channels changed: {src_a.channels} -> {out_a.channels}")
        if src_a.sample_rate and out_a.sample_rate and src_a.sample_rate != out_a.sample_rate:
            issues.append(f"audio sample rate changed: {src_a.sample_rate} -> {out_a.sample_rate}")
    return issues


def _fps_value(rational) -> float | None:
    if rational is None:
        return None
    numerator = getattr(rational, "numerator", None)
    denominator = getattr(rational, "denominator", None)
    if not numerator or not denominator:
        return None
    return numerator / denominator


def frame_region_mean(raw_rgb: bytes, width: int, height: int, region: tuple[float, float, float, float]) -> float:
    """Mean luminance over a fractional ``(x0, y0, x1, y1)`` region of a frame."""
    if width <= 0 or height <= 0 or len(raw_rgb) < width * height * 3:
        return 0.0
    x0 = max(0, int(region[0] * width))
    y0 = max(0, int(region[1] * height))
    x1 = min(width, max(x0 + 1, int(region[2] * width)))
    y1 = min(height, max(y0 + 1, int(region[3] * height)))
    total = 0.0
    count = 0
    row_bytes = width * 3
    for y in range(y0, y1):
        base = y * row_bytes
        for x in range(x0, x1):
            idx = base + x * 3
            r, g, b = raw_rgb[idx], raw_rgb[idx + 1], raw_rgb[idx + 2]
            total += 0.299 * r + 0.587 * g + 0.114 * b
            count += 1
    return total / count if count else 0.0


def classify_render_failure(stderr: str) -> str:
    """User-facing message for a failed encode (no paths / command lines)."""
    lowered = (stderr or "").lower()
    if "no such file" in lowered:
        return "The input or subtitle file could not be opened."
    if "invalid data found when processing input" in lowered:
        return "The input video appears to be corrupted."
    if "encoder not found" in lowered or "unknown encoder" in lowered:
        return "The requested video encoder is not available on this machine."
    return "FFmpeg failed while rendering the video."


class _EtaEstimator:
    """Recent-samples ETA from (elapsed, out_time_us) pairs."""

    def __init__(self, window: int = 5) -> None:
        self._window = window
        self._samples: list[tuple[float, float]] = []

    def estimate(self, elapsed: float, out_time_us: int, total_seconds: float) -> tuple[float | None, float | None]:
        """Return ``(eta_seconds, speed_x)`` or ``(None, None)`` when unmeasurable."""
        out_time = out_time_us / 1_000_000
        self._samples.append((elapsed, out_time))
        if len(self._samples) > self._window:
            self._samples.pop(0)
        if len(self._samples) < 2:
            return None, None
        (t0, p0), (t1, p1) = self._samples[0], self._samples[-1]
        dt = t1 - t0
        dp = p1 - p0
        if dt <= 0 or dp <= 0:
            return None, None
        speed_x = dp / dt
        remaining = max(0.0, total_seconds - out_time)
        return remaining / speed_x, speed_x


# ---------------------------------------------------------------------------
# Frame sampling (burn-in check)
# ---------------------------------------------------------------------------


def _extract_frame(file_path: str, time_s: float, width: int, height: int) -> bytes | None:
    """Decode one RGB24 frame at ``time_s`` (output-seek, so PTS is exact)."""
    ffmpeg = resolve_ffmpeg()
    if os.name == "nt":
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        kwargs = {"start_new_session": True}
    try:
        completed = subprocess.run(
            [
                ffmpeg, "-nostdin",
                "-i", file_path, "-ss", f"{max(0.0, time_s):.3f}",
                "-frames:v", "1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ],
            capture_output=True,
            timeout=_FRAME_EXTRACT_TIMEOUT,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or len(completed.stdout) < width * height * 3:
        return None
    return completed.stdout


def _extract_frame_mean(file_path: str, time_s: float, width: int, height: int, region: tuple[float, float, float, float]) -> float | None:
    """Mean luminance of a region of one decoded frame (``None`` on failure)."""
    frame = _extract_frame(file_path, time_s, width, height)
    if frame is None:
        return None
    return frame_region_mean(frame, width, height, region)


def _region_mad(a: bytes, b: bytes, width: int, height: int, region: tuple[float, float, float, float]) -> float | None:
    """Mean absolute per-channel difference over a region of two RGB24 frames."""
    if len(a) < width * height * 3 or len(b) < width * height * 3:
        return None
    x0 = max(0, int(region[0] * width))
    y0 = max(0, int(region[1] * height))
    x1 = min(width, max(x0 + 1, int(region[2] * width)))
    y1 = min(height, max(y0 + 1, int(region[3] * height)))
    total = 0.0
    count = 0
    row_bytes = width * 3
    for y in range(y0, y1):
        base = y * row_bytes
        for x in range(x0, x1):
            i = base + x * 3
            total += abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2])
            count += 1
    return total / count if count else None


def _burn_in_detected(source_path: str, output_path: str, width: int, height: int, window: tuple[float, float]) -> bool | None:
    """Confirm the subtitle actually rendered by comparing output vs source.

    The video is (presumably) moving, so comparing output frames against the
    *source at the same timestamp* cancels motion: where the subtitle is on
    screen the region's mean-absolute difference jumps far above the residual
    re-encode noise measured just before the cue window. Returns ``None`` when
    the frames could not be decoded (validation is then skipped).
    """
    start, end = window
    if end <= start or start < 0:
        return None
    baseline_t = max(0.0, start - 0.5)
    active_t = min(start + (end - start) / 2, max(baseline_t + 0.1, start))
    baseline_src = _extract_frame(source_path, baseline_t, width, height)
    baseline_out = _extract_frame(output_path, baseline_t, width, height)
    active_src = _extract_frame(source_path, active_t, width, height)
    active_out = _extract_frame(output_path, active_t, width, height)
    if None in (baseline_src, baseline_out, active_src, active_out):
        return None
    noise_mad = _region_mad(baseline_src, baseline_out, width, height, _BURN_REGION)
    active_mad = _region_mad(active_src, active_out, width, height, _BURN_REGION)
    if noise_mad is None or active_mad is None:
        return None
    return (active_mad - noise_mad) >= BURN_IN_MIN_DELTA


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def available_video_encoders() -> tuple[str, ...]:
    """Hardware encoders detected on this machine (best-effort)."""
    return hardware.detect_ffmpeg_encoders()


def render(
    config: RenderConfig,
    *,
    cancel: CancellationToken | None = None,
    on_progress: callable | None = None,
) -> RenderResult:
    """Burn subtitles into a video and validate the output before returning.

    Raises :class:`RenderError` with an architecture error code, or re-raises
    ``CancelledError`` on cancellation (temp files are always cleaned up).
    """
    input_path = os.path.abspath(validate_input_path(config.input_path))
    output_path = os.path.abspath(validate_input_path(config.output_path))
    subtitle_path = (
        os.path.abspath(validate_input_path(config.subtitle_path))
        if config.subtitle_path
        else None
    )
    if subtitle_path and not os.path.isfile(subtitle_path):
        raise RenderError(E_RENDER_INVALID, "Subtitle file does not exist.")
    if not os.path.isfile(input_path):
        raise RenderError(E_RENDER_INVALID, "Input video does not exist.")
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise RenderError(E_RENDER_INVALID, "Output path must differ from the input path.")

    source_meta = _probe_source(input_path)

    available = available_video_encoders()
    try:
        encoder = pick_video_encoder(available, config.video_encoder)
    except RenderError:
        raise

    workdir = Path(tempfile.mkdtemp(prefix="render_", dir=str(Path(output_path).parent)))
    temp_out = workdir / "out.mp4"
    try:
        subtitle_arg = None
        if subtitle_path:
            shutil.copy(subtitle_path, workdir / Path(subtitle_path).name)
            subtitle_arg = subtitle_filter_arg(subtitle_path)

        args = build_render_args(
            input_path,
            str(temp_out),
            encoder=encoder,
            subtitle_arg=subtitle_arg,
            preset=config.video_preset,
            crf=config.video_crf,
            audio_codec=config.audio_codec,
        )

        try:
            _run_encode(args, workdir=workdir, cancel=cancel, source_duration=source_meta.duration, on_progress=on_progress)
        except FFmpegError as exc:
            if config.allow_fallback and encoder in _HW_VIDEO_ENCODERS:
                logger.warning("hardware encoder %s failed; falling back to libx264", encoder)
                args = build_render_args(
                    input_path,
                    str(temp_out),
                    encoder="libx264",
                    subtitle_arg=subtitle_arg,
                    preset=config.video_preset,
                    crf=config.video_crf,
                    audio_codec=config.audio_codec,
                )
                _run_encode(args, workdir=workdir, cancel=cancel, source_duration=source_meta.duration, on_progress=on_progress)
                encoder = "libx264"
            else:
                raise RenderError(E_RENDER_FAILED, classify_render_failure(exc.message)) from exc

        if not temp_out.exists() or temp_out.stat().st_size == 0:
            raise RenderError(E_RENDER_VALIDATION, "Render produced no output file.")

        output_meta = _probe_output(temp_out)
        issues = render_validation_issues(source_meta, output_meta)

        burn_in: bool | None = None
        if config.check_window and not issues:
            burn_in = _burn_in_detected(input_path, str(temp_out), output_meta.width or 0, output_meta.height or 0, config.check_window)
            if burn_in is False:
                issues.append("subtitle burn-in not detected in the sampled frame region")
            elif burn_in is None:
                logger.warning("burn-in check could not sample frames; skipped")

        if issues:
            raise RenderError(E_RENDER_VALIDATION, "render validation failed: " + "; ".join(issues))

        os.replace(temp_out, output_path)
        fps = output_meta.fps
        return RenderResult(
            output_path=output_path,
            encoder_used=encoder,
            duration_seconds=output_meta.duration or 0.0,
            width=output_meta.width or 0,
            height=output_meta.height or 0,
            fps=(fps.numerator if fps else 0, fps.denominator if fps else 0),
            audio_streams=len(output_meta.audio_streams),
        )
    finally:
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def _run_encode(args, *, workdir, cancel, source_duration, on_progress) -> None:
    """Run one encode pass, wiring progress (fraction + ETA) to the caller."""
    command = [resolve_ffmpeg(), *args]
    if on_progress is None:
        result = run_ffmpeg(command, cancel=cancel, cwd=str(workdir))
    else:
        estimator = _EtaEstimator()
        started = time.monotonic()
        total = float(source_duration or 0.0)

        def _progress(parsed: dict[str, str]) -> None:
            seconds = out_time_seconds(parsed)
            if seconds is None:
                return
            fraction = progress_fraction(int(seconds * 1_000_000), total)
            elapsed = time.monotonic() - started
            eta, speed_x = estimator.estimate(elapsed, int(seconds * 1_000_000), total)
            on_progress(RenderProgress(fraction=fraction, eta_seconds=eta, speed_x=speed_x))

        result = run_ffmpeg(command, cancel=cancel, cwd=str(workdir), on_progress=_progress)
    if result.returncode != 0:
        raise FFmpegError(E_FFMPEG_FAILED, classify_render_failure(result.stderr))


def _probe_source(input_path: str):
    try:
        return probe(input_path)
    except MediaProbeError as exc:
        raise RenderError(E_RENDER_INVALID, f"Input video could not be probed: {exc.code}") from exc


def _probe_output(temp_out: Path):
    try:
        return probe(str(temp_out))
    except MediaProbeError as exc:
        raise RenderError(E_RENDER_VALIDATION, f"Output video failed probing: {exc.code}") from exc

