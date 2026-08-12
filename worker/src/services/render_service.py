"""RenderService (TASK-027/028): libass burn-in + watermark + auto encoder.

Renders a video with subtitles burned in via the ``ass``/``subtitles`` libass
filter and optional text/image watermarks via ``drawtext``/``overlay``
(MASTER_PLAN §3.7), then **validates the output before it is ever handed to the
caller** — a corrupt or wrong file is never shipped silently.

Pipeline (MASTER_PLAN §9.2 / TASK-027/028):

1. Probe the input (resolution / FPS / audio / duration) with ffprobe.
2. Pick a video encoder: hardware NVENC → QSV → AMF, else libx264; a requested
   encoder may be forced, with graceful fallback to libx264 on hardware failure.
3. Encode straight to a temp file in the destination directory (same volume, so
   the final ``os.replace`` is atomic), keeping resolution / FPS / SAR / colour
   metadata untouched (no scaling, no re-timeline). A watermark (text via
   ``drawtext`` with correct filter-graph escaping, image via ``overlay`` on the
   scaled/alpha-treated overlay stream) is wired into the same ``-vf`` or
   ``-filter_complex`` pass.
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
subtitle/watermark files are copied into the temp workdir under a safe generated
name and referenced through the process ``cwd`` so no filter-graph path escaping
is needed; watermark text is escaped for the filter-graph grammar (a literal
``'`` that cannot be escaped is routed through a ``textfile=`` payload); error
messages never embed paths or command lines.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
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
from src.services import cache, hardware
from src.services.media_service import MediaProbeError, probe

logger = logging.getLogger(__name__)

E_RENDER_INVALID = "E_RENDER_INVALID"
E_RENDER_FAILED = "E_RENDER_FAILED"
E_RENDER_VALIDATION = "E_RENDER_VALIDATION"

# Export / QC errors (TASK-029; MASTER_PLAN §28.1 table).
E_EXPORT_INVALID = "E_EXPORT_INVALID"
E_EXPORT_QC = "E_EXPORT_QC"
E_PERMISSION_DENIED = "E_PERMISSION_DENIED"
E_DISK_FULL = "E_DISK_FULL"

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

# Named watermark positions (MASTER_PLAN §3.7 / TASK-028): a 3x3 grid plus a
# fully custom ``x``/``y`` position.
WM_POSITIONS: tuple[str, ...] = (
    "top-left", "top", "top-right",
    "left", "center", "right",
    "bottom-left", "bottom", "bottom-right",
)

# Characters that are special inside an ffmpeg filter-graph option value and are
# escaped by ``escape_drawtext``; ``'`` is deliberately NOT in this set because
# ffmpeg's filtergraph parser cannot express a literal single quote inside a
# quoted value — text containing one must be routed through a ``textfile=``
# payload (see ``build_drawtext_filter``), and ``escape_drawtext`` rejects it.
_DRAWTEXT_ESC_CHARS = frozenset("\\:,[]%{};")


class RenderError(Exception):
    """Render failure carrying the architecture error code (MASTER_PLAN §28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TextWatermark:
    """Text watermark burned via the ffmpeg ``drawtext`` filter (TASK-028).

    ``position`` is one of :data:`WM_POSITIONS`, or ``"custom"`` to pin the
    watermark at ``(x, y)``. ``font`` is a font family name when the system font
    service can resolve it; ``font_file`` is an explicit font path (copied into
    the render workdir so no graph escaping of the path is ever needed).
    """

    text: str
    position: str = "bottom-right"
    margin: int = 24
    x: int = 0
    y: int = 0
    font_size: int = 48
    color: str = "#FFFFFFFF"
    opacity: float = 1.0
    rotation: float = 0.0
    font: str | None = None
    font_file: str | None = None


@dataclass(frozen=True)
class ImageWatermark:
    """Image watermark burned via the ffmpeg ``overlay`` filter (TASK-028).

    ``width`` scales the overlay to a target width in pixels (0 keeps the image's
    original size); ``opacity`` in ``(0..1]`` is applied to the alpha channel.
    """

    image_path: str
    position: str = "bottom-right"
    margin: int = 24
    x: int = 0
    y: int = 0
    width: int = 0
    opacity: float = 1.0


@dataclass(frozen=True)
class WatermarkConfig:
    """Immutable watermark set; at most one text and one image watermark."""

    text: TextWatermark | None = None
    image: ImageWatermark | None = None

    @property
    def enabled(self) -> bool:
        return self.text is not None or self.image is not None


@dataclass(frozen=True)
class RenderConfig:
    """Inputs for one burn-in render.

    ``video_encoder=None`` auto-detects (NVENC → QSV → AMF → libx264).
    ``check_window`` is the ``(start, end)`` seconds window where a subtitle is
    displayed; when provided, the output must show a text region there.
    ``watermark`` carries optional text/image watermarks; when present, the
    output is additionally validated to show them in their configured regions.
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
    watermark: WatermarkConfig | None = None
    #: Optional full-duration voice track (``/v1/tts/synthesize`` output) to
    #: mix over the original audio (original ducked to ~45%).
    voice_track_path: str | None = None


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


@dataclass(frozen=True)
class ExportQCReport:
    """QC verdict for an exported file (TASK-029).

    ``passed`` is ``False`` when at least one mandatory check failed; any
    ``warnings`` are informational only (e.g. muxed subtitle streams).
    """

    passed: bool
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportResult:
    """Outcome of :func:`export_video`: final path + QC report."""

    path: str
    qc: ExportQCReport


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
    filter_graph: _FilterGraph | None = None,
    preset: str = DEFAULT_RENDER_PRESET,
    crf: int = DEFAULT_RENDER_CRF,
    audio_codec: str = DEFAULT_AUDIO_CODEC,
    audio_path: str | None = None,
) -> list[str]:
    """Argument array for a burn-in render.

    ``filter_graph`` wins over ``subtitle_arg``: a pre-built filter graph (with
    an optional extra input for an image watermark) is emitted verbatim, wiring
    ``-map`` to the graph's ``[vout]`` label instead of ``0:v:0``.

    Software encoders get ``-preset``/``-crf``; hardware encoders use their own
    quality defaults (nvenc/qsv/amf do not share libx264's flags). Streams are
    mapped explicitly so all audio is preserved and no stream is dropped.
    """
    validate_input_path(input_path)
    validate_input_path(output_path)
    args = ["-y", "-nostdin", "-i", input_path]
    if audio_path:
        validate_input_path(audio_path)
        args += ["-i", audio_path]
    if filter_graph is not None:
        if filter_graph.extra_input:
            validate_input_path(filter_graph.extra_input)
            args += ["-i", filter_graph.extra_input]
        args += [filter_graph.option, filter_graph.value]
        if filter_graph.option == "-filter_complex":
            args += ["-map", "[vout]"]
        else:
            args += ["-map", "0:v:0"]
    elif subtitle_arg:
        args += ["-vf", subtitle_arg, "-map", "0:v:0"]
    else:
        args += ["-map", "0:v:0"]
    if audio_path:
        # The last ``-i`` is the provided audio track; map it directly.
        args += ["-map", f"{args.count('-i') - 1}:a"]
    else:
        args += ["-map", "0:a?"]
    args += ["-c:v", encoder]
    if encoder in _SOFTWARE_VIDEO_ENCODERS:
        args += ["-preset", preset, "-crf", str(crf)]
    args += ["-c:a", audio_codec, "-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    args.append(output_path)
    return args


def _probe_audio_format(path: str) -> tuple[int, int]:
    """Return ``(channels, sample_rate)`` of the first audio stream.

    Falls back to stereo 44.1 kHz when the probe fails or the file has no
    audio — matching the pre-existing mix default.
    """
    try:
        meta = probe(path)
        if meta.audio_streams:
            a = meta.audio_streams[0]
            return (a.channels or 2, a.sample_rate or 44100)
    except MediaProbeError:
        pass
    return 2, 44100


def _mix_voice_track(input_path: str, voice_track_path: str, out_wav: str, source_has_audio: bool) -> None:
    """Mix the TTS voice track over the original audio (original ducked ~45%).

    ``out_wav`` receives a PCM track in the *source's* channel layout and
    sample rate (so render QC's format-preservation checks still pass): voice
    at full volume over the ducked original (background/music preserved
    underneath). When the source has no audio, the voice track becomes the
    whole mix in the voice track's own format.
    """
    if source_has_audio:
        channels, sample_rate = _probe_audio_format(input_path)
        args = [
            "ffmpeg", "-y", "-nostdin",
            "-i", input_path,
            "-i", voice_track_path,
            "-filter_complex",
            "[0:a]volume=0.45[orig];[1:a]volume=1.0[voice];"
            "[orig][voice]amix=inputs=2:duration=first:normalize=0[aout]",
            "-map", "[aout]",
            "-ac", str(channels), "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            out_wav,
        ]
    else:
        channels, sample_rate = _probe_audio_format(voice_track_path)
        args = [
            "ffmpeg", "-y", "-nostdin",
            "-i", voice_track_path,
            "-ac", str(channels), "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            out_wav,
        ]
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RenderError(E_RENDER_FAILED, "ffmpeg not found while mixing the voice track.") from exc
    if proc.returncode != 0:
        raise RenderError(
            E_RENDER_FAILED,
            f"voice-track mix failed: {(proc.stderr or '').strip()[-400:]}",
        )


def subtitle_filter_arg(subtitle_path: str) -> str:
    """Filter-graph reference for a subtitle file copied into the workdir."""
    name = Path(subtitle_path).name.lower()
    if name.endswith(".ass"):
        return f"ass={Path(subtitle_path).name}"
    return f"subtitles={Path(subtitle_path).name}"


# ---------------------------------------------------------------------------
# Watermark filter building (TASK-028)
# ---------------------------------------------------------------------------


def escape_drawtext(text: str) -> str:
    """Escape an arbitrary string for drawtext's ``text=`` option.

    Returns a single-quoted token where every filter-graph special character is
    backslash-escaped, so it can be embedded verbatim in ``-vf``/`-filter_complex``.
    ffmpeg decodes the escapes and the quoted section, so the rendered text is
    byte-identical to the input. A literal ``'`` cannot be represented this way
    (ffmpeg's filtergraph parser has no escape for it inside a quoted value) and
    is rejected — callers must route such text through ``textfile=`` instead (see
    ``build_drawtext_filter``).
    """
    if not isinstance(text, str):
        raise RenderError(E_RENDER_INVALID, "Watermark text must be a string.")
    if "'" in text:
        raise RenderError(
            E_RENDER_INVALID,
            "Watermark text containing a single quote must be routed through a text file.",
        )
    escaped = "".join(f"\\{ch}" if ch in _DRAWTEXT_ESC_CHARS else ch for ch in text)
    return f"'{escaped}'"


def _anchor_exprs(position: str, margin: int, *, box_w: str, box_h: str, base_w: str, base_h: str) -> tuple[str, str]:
    """Return ``(x, y)`` filter expressions for a named position/anchor.

    ``box_w``/``box_h`` name the watermark's measured filter variables
    (``text_w``/``text_h`` for drawtext, ``overlay_w``/``overlay_h`` for
    overlay); ``base_w``/``base_h`` name the main frame's dimension (``w``/``h``
    for drawtext — those are the input frame there — but ``main_w``/``main_h``
    for overlay, where ``w``/``h`` alias the *overlay* size, not the frame).
    Raises ``RenderError`` for any position outside the 3x3 grid.
    """
    if position == "custom":
        raise RenderError(E_RENDER_INVALID, "custom position needs explicit x/y")
    edge_w = f"{base_w}-{box_w}"
    edge_h = f"{base_h}-{box_h}"
    half_x = f"({edge_w})/2"
    half_y = f"({edge_h})/2"
    m = int(margin)
    positions = {
        "top-left": (str(m), str(m)),
        "top": (half_x, str(m)),
        "top-right": (f"{edge_w}-{m}", str(m)),
        "left": (str(m), half_y),
        "center": (half_x, half_y),
        "right": (f"{edge_w}-{m}", half_y),
        "bottom-left": (str(m), f"{edge_h}-{m}"),
        "bottom": (half_x, f"{edge_h}-{m}"),
        "bottom-right": (f"{edge_w}-{m}", f"{edge_h}-{m}"),
    }
    if position not in positions:
        raise RenderError(E_RENDER_INVALID, f"unknown watermark position {position!r}")
    return positions[position]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class _FilterGraph:
    """A completed ``-vf`` or ``-filter_complex`` value plus an optional 2nd input."""

    option: str  # "-vf" | "-filter_complex"
    value: str
    extra_input: str | None = None


def build_drawtext_filter(wm: TextWatermark, *, textfile: str | None = None, fontfile: str | None = None) -> str:
    """Full ``drawtext=...`` filter string for a text watermark.

    Two input routes for the payload:
    - ``textfile`` — when the watermark text contains a literal ``'`` (the one
      character ffmpeg cannot express inside a quoted drawtext value), the caller
      writes the text to a UTF-8 file in the workdir and passes its bare name.
    - otherwise the text is inlined and escaped by ``escape_drawtext``.

    ``fontfile`` overrides the font source with a bare workdir name (the renderer
    copies ``wm.font_file`` there), so no path escaping is ever needed.
    """
    if wm.position == "custom":
        x, y = str(int(wm.x)), str(int(wm.y))
    else:
        x, y = _anchor_exprs(wm.position, wm.margin, box_w="text_w", box_h="text_h", base_w="w", base_h="h")
    parts: list[str] = ["drawtext="]
    if textfile is not None:
        parts.append(f"textfile={textfile}")
    else:
        parts.append(f"text={escape_drawtext(wm.text)}")
    parts.append("expansion=none")
    if fontfile is not None:
        parts.append(f"fontfile={fontfile}")
    elif wm.font_file:
        parts.append(f"fontfile={Path(wm.font_file).name}")
    elif wm.font:
        parts.append(f"font={escape_drawtext(wm.font)}")
    parts.append(f"fontsize={int(wm.font_size)}")
    parts.append(f"fontcolor={wm.color}")
    if wm.opacity < 1.0:
        parts.append(f"alpha={_clamp01(wm.opacity):.3f}")
    parts.append(f"x={x}")
    parts.append(f"y={y}")
    if wm.rotation:
        parts.append(f"rotation={wm.rotation:.4f}")
    return ":".join(parts)


def build_overlay_filter(wm: ImageWatermark) -> tuple[str, str]:
    """Return ``(prep, overlay)`` fragments for an image watermark.

    ``prep`` transforms the ``[1:v]`` image stream (scale + alpha); ``overlay``
    is the ``overlay=x:y`` filter that the graph joins as
    ``[vbase][wmimg]<overlay>[vout]``.
    """
    if wm.position == "custom":
        x, y = str(int(wm.x)), str(int(wm.y))
    else:
        x, y = _anchor_exprs(wm.position, wm.margin, box_w="overlay_w", box_h="overlay_h", base_w="main_w", base_h="main_h")
    prep_parts: list[str] = []
    if wm.width and wm.width > 0:
        prep_parts.append(f"scale={int(wm.width)}:-2:flags=lanczos")
    opacity = _clamp01(wm.opacity)
    if opacity < 1.0:
        prep_parts.append(f"format=rgba,colorchannelmixer=aa={opacity:.3f}")
    prep = ",".join(prep_parts)
    return prep, f"overlay={x}:{y}"


def build_filter_graph(
    *,
    subtitle_arg: str | None = None,
    text_watermark: TextWatermark | None = None,
    image_watermark: ImageWatermark | None = None,
    textfile: str | None = None,
    fontfile: str | None = None,
    image_input: str | None = None,
) -> _FilterGraph | None:
    """Compose subtitle burn-in + text/image watermark into one filter graph.

    Returns ``None`` when there is nothing to filter. A pure text/subtitle chain
    stays a single ``-vf``; an image watermark adds a second input and is emitted
    as ``-filter_complex`` with explicit labels.
    """
    chain = [
        f
        for f in (
            subtitle_arg,
            build_drawtext_filter(text_watermark, textfile=textfile, fontfile=fontfile)
            if text_watermark is not None
            else None,
        )
        if f
    ]
    if image_watermark is None:
        value = ",".join(chain)
        if not value:
            return None
        return _FilterGraph("-vf", value)
    prep, overlay_text = build_overlay_filter(image_watermark)
    pieces = [
        f"[0:v]{','.join(chain) if chain else 'null'}[vbase]",
        f"[1:v]{prep if prep else 'null'}[wmimg]",
        f"[vbase][wmimg]{overlay_text}[vout]",
    ]
    return _FilterGraph("-filter_complex", ";".join(pieces), extra_input=image_input)


def watermark_fingerprint(watermark: WatermarkConfig | None) -> str:
    """Canonical serialization fed into the render cache key (TASK-028).

    Content-addressed for image files (SHA-256) so replacing the watermark image
    — or editing any text/position/opacity param — changes the key and misses the
    render cache, per ARCHITECTURE_DECISION.md §3.7. ``None`` maps to ``"none"``.
    """
    if watermark is None or not watermark.enabled:
        return "none"
    if watermark.text is not None:
        t = watermark.text
        return json.dumps(
            {
                "kind": "text",
                "text": t.text,
                "position": t.position,
                "margin": t.margin,
                "x": t.x,
                "y": t.y,
                "font": t.font,
                "font_file": t.font_file,
                "font_size": t.font_size,
                "color": t.color,
                "opacity": round(t.opacity, 4),
                "rotation": round(t.rotation, 4),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    img = watermark.image
    digest = cache.sha256_file(img.image_path) if img.image_path and os.path.isfile(img.image_path) else ""
    return json.dumps(
        {
            "kind": "image",
            "image_sha256": digest,
            "image_path": img.image_path,
            "position": img.position,
            "margin": img.margin,
            "x": img.x,
            "y": img.y,
            "width": img.width,
            "opacity": round(img.opacity, 4),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


_IMAGE_WATERMARK_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def validate_watermark(watermark: WatermarkConfig | None) -> None:
    """Reject malformed watermark inputs before any workdir is created."""
    if watermark is None or not watermark.enabled:
        return
    for wm, kind in ((watermark.text, "text"), (watermark.image, "image")):
        if wm is None:
            continue
        if wm.position != "custom" and wm.position not in WM_POSITIONS:
            raise RenderError(
                E_RENDER_INVALID, f"unknown {kind} watermark position {wm.position!r}"
            )
        if not (0.0 <= wm.opacity <= 1.0):
            raise RenderError(
                E_RENDER_INVALID, f"{kind} watermark opacity must be within 0..1"
            )
    if watermark.text is not None:
        if not watermark.text.text:
            raise RenderError(E_RENDER_INVALID, "watermark text must not be empty")
        if watermark.text.position == "custom" and (watermark.text.x < 0 or watermark.text.y < 0):
            raise RenderError(E_RENDER_INVALID, "custom text watermark position must be non-negative")
        if watermark.text.font_size <= 0:
            raise RenderError(E_RENDER_INVALID, "watermark font size must be positive")
        if watermark.text.font_file and not os.path.isfile(watermark.text.font_file):
            raise RenderError(E_RENDER_INVALID, "watermark font file does not exist")
    if watermark.image is not None:
        img = watermark.image
        img_path = os.path.abspath(img.image_path)
        if not os.path.isfile(img_path):
            raise RenderError(E_RENDER_INVALID, "watermark image does not exist")
        if Path(img_path).suffix.lower() not in _IMAGE_WATERMARK_EXTS:
            raise RenderError(E_RENDER_INVALID, "watermark image must be PNG, JPG or WebP")
        if img.position == "custom" and (img.x < 0 or img.y < 0):
            raise RenderError(E_RENDER_INVALID, "custom image watermark position must be non-negative")
        if img.width < 0:
            raise RenderError(E_RENDER_INVALID, "watermark image width must be non-negative")


def prepare_watermark(workdir: Path, watermark: WatermarkConfig | None) -> tuple[str | None, str | None, str | None]:
    """Copy watermark assets under the render workdir.

    Returns ``(textfile_name, fontfile_name, image_input_path)``:
    - ``textfile_name`` — bare workdir filename of a UTF-8 payload file when the
      watermark text contains a literal ``'`` (the one character ffmpeg cannot
      embed in a quoted drawtext value); ``None`` when the text is inlined.
    - ``fontfile_name`` — bare workdir filename of the copied font file, when a
      custom font was requested; ``None`` to use drawtext's default font.
    - ``image_input_path`` — absolute path of the copied watermark image (added
      as a second ffmpeg input); ``None`` when there is no image watermark.

    All files are copied under generated names so no filter-graph value ever
    contains a path (security model, mirroring subtitle handling).
    """
    validate_watermark(watermark)
    if watermark is None or not watermark.enabled:
        return None, None, None
    textfile = None
    fontfile = None
    image_input = None

    if watermark.text is not None:
        wm = watermark.text
        if "'" in wm.text:
            payload = workdir / "wm_text.txt"
            payload.write_text(wm.text, encoding="utf-8")
            textfile = payload.name
        if wm.font_file:
            copied = workdir / "wm_font.bin"
            shutil.copy2(wm.font_file, copied)
            fontfile = copied.name
    if watermark.image is not None:
        img = watermark.image
        ext = Path(img.image_path).suffix.lower() or ".png"
        copied = workdir / f"wm_image{ext}"
        shutil.copy2(img.image_path, copied)
        image_input = os.path.abspath(os.path.join(workdir, copied.name))
    return textfile, fontfile, image_input


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

        audio_path = None
        if config.voice_track_path:
            voice_track = os.path.abspath(validate_input_path(config.voice_track_path))
            if not os.path.isfile(voice_track):
                raise RenderError(E_RENDER_INVALID, "Voice track does not exist.")
            mixed = workdir / "mixed_audio.wav"
            _mix_voice_track(
                input_path,
                voice_track,
                str(mixed),
                bool(source_meta.audio_streams),
            )
            audio_path = str(mixed)

        textfile, fontfile, image_input = prepare_watermark(workdir, config.watermark)
        filter_graph = build_filter_graph(
            subtitle_arg=subtitle_arg,
            text_watermark=config.watermark.text if config.watermark else None,
            image_watermark=config.watermark.image if config.watermark else None,
            textfile=textfile,
            fontfile=fontfile,
            image_input=image_input,
        )

        args = build_render_args(
            input_path,
            str(temp_out),
            encoder=encoder,
            filter_graph=filter_graph,
            preset=config.video_preset,
            crf=config.video_crf,
            audio_codec=config.audio_codec,
            audio_path=audio_path,
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
                    filter_graph=filter_graph,
                    preset=config.video_preset,
                    crf=config.video_crf,
                    audio_codec=config.audio_codec,
                    audio_path=audio_path,
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


# ---------------------------------------------------------------------------
# Export + QC (TASK-029)
# ---------------------------------------------------------------------------

EXPORT_SUBTITLE_FORMATS: tuple[str, ...] = ("srt", "vtt", "ass")


@dataclass(frozen=True)
class SubtitleExportOptions:
    """Export options for a subtitle file (TASK-029).

    ``format`` is one of ``srt`` / ``vtt`` / ``ass``; ``None`` copies the source
    file extension as-is. ``name`` overrides the output file stem.
    """

    format: str | None = None
    name: str | None = None


def export_video(
    source_video: str,
    target_dir: str,
    *,
    name: str | None = None,
    run_qc: bool = True,
) -> ExportResult:
    """Copy a rendered video into ``target_dir`` and QC it before reporting.

    The destination file is written atomically (copy to a temp name in the same
    directory, then ``os.replace``) so a partially-written export is never left
    behind. On collision an automatic `` (1)``, `` (2)``, … suffix is appended.
    When ``run_qc`` is true the copied file is probed and validated against the
    criteria in :func:`render_validation_issues`; on hard failure the temp file
    is removed and :class:`RenderError` with ``E_EXPORT_QC`` is raised.

    Raises :class:`RenderError`:
    - ``E_PERMISSION_DENIED`` — target directory cannot be created/written.
    - ``E_DISK_FULL``         — not enough free space on the destination drive.
    - ``E_EXPORT_INVALID``    — source video missing.
    - ``E_EXPORT_QC``         — exported file failed a mandatory QC check.
    """
    source = os.path.abspath(source_video)
    if not os.path.isfile(source):
        raise RenderError(E_EXPORT_INVALID, "Source video does not exist.")
    directory = _prepare_export_dir(target_dir)
    stem = (name or os.path.splitext(os.path.basename(source))[0]).strip()
    if not stem:
        raise RenderError(E_EXPORT_INVALID, "Export file name is empty.")
    ext = os.path.splitext(source)[1] or ".mp4"
    final_path = _unique_path(directory, stem, ext)

    qc: ExportQCReport | None = None
    try:
        if run_qc:
            source_meta = _probe_source(source)
        else:
            source_meta = None
        tmp = _copy_with_guards(source, final_path)
        if run_qc:
            try:
                output_meta = probe(str(tmp))
            except MediaProbeError as exc:
                raise RenderError(E_EXPORT_QC, f"Exported video failed probing: {exc.code}") from exc
            qc = build_qc_report(source_meta, output_meta)
            if not qc.passed:
                raise RenderError(E_EXPORT_QC, "export QC failed: " + "; ".join(qc.issues))
        os.replace(tmp, final_path)
    except RenderError:
        _cleanup_export_tmp(final_path)
        raise
    return ExportResult(path=final_path, qc=qc or ExportQCReport(passed=True))


def build_qc_report(source_meta: MediaMetadata, output_meta: MediaMetadata) -> ExportQCReport:
    """QC verdict comparing an exported file against the source criteria.

    Reuses the mandatory checks from :func:`render_validation_issues`
    (resolution, FPS, duration ±1 s, codec, audio preservation) and adds the
    container-validity criteria from MASTER_PLAN §26.2 (video stream present,
    subtitle streams preserved when the source had them).
    """
    issues = render_validation_issues(source_meta, output_meta)
    warnings: list[str] = []
    if not output_meta.video_streams:
        issues.append("exported file has no video stream")
    if not source_meta.video_streams and not output_meta.video_streams:
        issues.append("exported file is not a video")
    elif source_meta.subtitle_streams and not output_meta.subtitle_streams:
        warnings.append("muxed subtitle streams were dropped")
    return ExportQCReport(passed=not issues, issues=tuple(issues), warnings=tuple(warnings))


def export_subtitles(
    source_subtitle: str,
    target_dir: str,
    *,
    options: SubtitleExportOptions | None = None,
) -> str:
    """Export a subtitle file into ``target_dir``, optionally converting format.

    ``options.format`` may be ``srt``, ``vtt`` or ``ass``. ``srt`` and ``vtt``
    are converted losslessly; ``ass`` is copied as-is (and cannot be converted
    to another format here — that requires an ASS parser, out of MVP scope).
    On collision the file is written with an automatic `` (1)`` suffix.

    Raises :class:`RenderError`:
    - ``E_PERMISSION_DENIED`` — target directory cannot be created/written.
    - ``E_DISK_FULL``         — not enough free space.
    - ``E_EXPORT_INVALID``    — source missing or an unsupported conversion.
    """
    opts = options or SubtitleExportOptions()
    source = os.path.abspath(source_subtitle)
    if not os.path.isfile(source):
        raise RenderError(E_EXPORT_INVALID, "Source subtitle file does not exist.")
    directory = _prepare_export_dir(target_dir)
    stem = (opts.name or os.path.splitext(os.path.basename(source))[0]).strip()
    if not stem:
        raise RenderError(E_EXPORT_INVALID, "Export file name is empty.")
    src_ext = os.path.splitext(source)[1].lstrip(".").lower()
    if src_ext not in EXPORT_SUBTITLE_FORMATS:
        raise RenderError(E_EXPORT_INVALID, f"unsupported source subtitle format: {src_ext!r}")
    target_fmt = opts.format or src_ext
    if target_fmt not in EXPORT_SUBTITLE_FORMATS:
        raise RenderError(E_EXPORT_INVALID, f"unsupported subtitle export format: {target_fmt!r}")
    if src_ext == "ass" and target_fmt != "ass":
        raise RenderError(E_EXPORT_INVALID, "converting ASS subtitles is not supported")
    final_path = _unique_path(directory, stem, "." + target_fmt)

    try:
        tmp = final_path + ".tmp"
        if target_fmt == src_ext:
            _copy_without_qc(source, tmp)
        else:
            text = _read_subtitle_text(source)
            converted = srt_to_vtt(text) if (src_ext, target_fmt) == ("srt", "vtt") else vtt_to_srt(text)
            _write_subtitle_text(tmp, converted)
        os.replace(tmp, final_path)
    except RenderError:
        _cleanup_export_tmp(final_path)
        raise
    return final_path


# -- export internals -------------------------------------------------------


def _prepare_export_dir(target_dir: str) -> str:
    """Create/write-check the destination directory; map failures to architecture codes."""
    directory = os.path.abspath(target_dir)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise _as_render_error(exc, "Không có quyền ghi vào thư mục.") from exc
    if not os.path.isdir(directory):
        raise RenderError(E_EXPORT_INVALID, "Export target is not a directory.")
    probe = os.path.join(directory, ".tc__write_probe")
    try:
        with open(probe, "wb"):
            pass
    except OSError as exc:
        raise _as_render_error(exc, "Không có quyền ghi vào thư mục.") from exc
    finally:
        with contextlib.suppress(OSError):
            os.remove(probe)
    return directory


def _as_render_error(exc: OSError, perm_message: str) -> RenderError:
    if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return RenderError(E_PERMISSION_DENIED, "Không có quyền ghi vào thư mục.")
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return RenderError(E_DISK_FULL, "Đĩa không đủ dung lượng.")
    return RenderError(E_EXPORT_INVALID, perm_message)


def _copy_with_guards(source: str, final_path: str) -> str:
    """Copy to a sibling temp file, mapping OSError to architecture codes."""
    tmp = final_path + ".tmp"
    try:
        shutil.copy2(source, tmp)
    except OSError as exc:
        raise _as_render_error(exc, "Không thể ghi file export.") from exc
    return tmp


def _copy_without_qc(source: str, tmp: str) -> None:
    try:
        shutil.copy2(source, tmp)
    except OSError as exc:
        raise _as_render_error(exc, "Không thể ghi file export.") from exc


def _read_subtitle_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RenderError(E_EXPORT_INVALID, "Không đọc được file subtitle.") from exc


def _write_subtitle_text(path: str, text: str) -> None:
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError as exc:
        raise _as_render_error(exc, "Không thể ghi file export.") from exc


def _unique_path(directory: str, stem: str, ext: str) -> str:
    """First free ``stem.ext``, ``stem (1).ext``, ``stem (2).ext``, …"""
    candidate = os.path.join(directory, f"{stem}{ext}")
    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem} ({index}){ext}")
        index += 1
    return candidate


def _cleanup_export_tmp(final_path: str) -> None:
    tmp = final_path + ".tmp"
    with contextlib.suppress(OSError):
        if os.path.exists(tmp):
            os.remove(tmp)


def srt_to_vtt(srt_text: str) -> str:
    """Convert SRT content to WebVTT (comma separators become dots).

    Pure text transform: indexes are dropped (VTT needs none) and a ``WEBVTT``
    header is prepended. Cue timings and text are preserved verbatim.
    """
    cues: list[tuple[str, str]] = []

    def parse_block(lines: list[str]) -> None:
        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            return
        timing = lines[timing_idx].replace(",", ".")
        text = "\n".join(lines[timing_idx + 1:]).strip()
        cues.append((timing, text))

    _parse_blocks(srt_text, parse_block)
    out: list[str] = ["WEBVTT", ""]
    for timing, text in cues:
        if text:
            out.append(f"{timing}\n{text}")
        else:
            out.append(timing)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def vtt_to_srt(vtt_text: str) -> str:
    """Convert WebVTT content to SRT (dots become commas, cue indices added)."""
    cues: list[tuple[str, str]] = []

    def parse_block(lines: list[str]) -> None:
        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            return
        timing = lines[timing_idx].replace(".", ",")
        text = "\n".join(lines[timing_idx + 1:]).strip()
        cues.append((timing, text))

    _parse_blocks(vtt_text, parse_block)

    out: list[str] = []
    for index, (timing, text) in enumerate(cues, start=1):
        out.append(str(index))
        out.append(timing)
        if text:
            out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _parse_blocks(text: str, process: Callable[[list[str]], None]) -> None:
    """Split ``text`` on blank lines and run ``process`` over each block's lines."""
    block: list[str] = []
    for raw in text.splitlines():
        if raw.strip():
            if raw.strip().isdigit():
                continue
            block.append(raw)
        elif block:
            process(block)
            block = []
    if block:
        process(block)

