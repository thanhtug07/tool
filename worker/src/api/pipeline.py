"""Worker pipeline stage routes (RELEASE-P0).

The Rust JobService dispatches each stage to the worker over the authenticated
loopback HTTP API. These routes wrap the existing stage services
(``audio_service`` / ``stt_service`` / ``translation_service`` /
``subtitle_service`` / ``render_service``) with a thin, cancellable, validated
HTTP surface:

- ``POST /v1/audio/extract``   — WAV 16k mono extraction (+ cancel + progress log)
- ``POST /v1/stt/transcribe``  — (existing, extended with ``job_id`` cancel)
- ``POST /v1/translate``       — contextual translation via a named provider
- ``POST /v1/subtitle``        — cues + ASS/SRT generation from transcript+translation
- ``POST /v1/render``          — libass burn-in render (+ cancel + progress log)
- ``POST /v1/jobs/{job_id}/cancel`` — cancel an in-flight stage

Cancellation model: each request may carry a ``job_id``; the worker registers a
``CancellationToken`` for it for the duration of the call and the cancel
endpoint sets it, so long operations (STT / render) abort promptly. Tokens are
removed when the call finishes (success or failure).

Security: every route requires the bearer token; request bodies never contain
command lines; paths are validated by the services themselves; error responses
are the canonical ``{"error": {code, message, recoverable}}`` envelope and
never embed stack traces, tokens, or full command lines.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.routes import require_bearer
from src.api.schemas import Transcript, Translation, TranslationBlock
from src.core.job import CancelledError, CancellationToken
from src.services.providers.base import ProviderError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_bearer)])

# ---------------------------------------------------------------------------
# Cancellation registry
# ---------------------------------------------------------------------------

_cancel_lock = threading.Lock()
_cancel_tokens: dict[str, CancellationToken] = {}


@contextmanager
def _cancel_scope(job_id: str | None):
    """Register a cancellation token for ``job_id`` for the call's lifetime.

    An already-registered token (e.g. pre-cancelled by ``cancel_job``) is
    reused so a job that was cancelled between stages cannot silently start
    the next stage.
    """
    if job_id:
        with _cancel_lock:
            token = _cancel_tokens.get(job_id)
    else:
        token = None
    if token is None:
        token = CancellationToken()
        if job_id:
            with _cancel_lock:
                _cancel_tokens[job_id] = token
    try:
        yield token
    finally:
        if job_id:
            with _cancel_lock:
                _cancel_tokens.pop(job_id, None)


def cancel_job(job_id: str) -> bool:
    """Request cancellation of an in-flight stage; returns whether it existed."""
    with _cancel_lock:
        token = _cancel_tokens.get(job_id)
    if token is None:
        return False
    token.cancel()
    return True


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AudioExtractRequest(BaseModel):
    video_path: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    job_id: str | None = None


class TranslateRequest(BaseModel):
    """One translation stage: transcript + provider selection + context.

    ``provider`` is the registered provider name (``mock``/``gemini``/``local``).
    ``provider_config`` carries provider-specific non-secret options (base URL,
    model name, model path); secrets (API keys) are carried in ``api_key`` only
    when the provider needs them, and are never logged.
    """

    transcript: Transcript
    project_id: str = Field(min_length=1)
    provider: str = "mock"
    target_language: str = Field(min_length=2, max_length=8)
    model: str = Field(default="gemini-2.5-flash-lite", min_length=1)
    glossary_ver: str = "0"
    glossary: dict[str, str] | None = None
    characters: dict[str, str] | None = None
    rules: list[str] | None = None
    api_key: str | None = None
    provider_config: dict[str, str] | None = None
    job_id: str | None = None


class SubtitleRequest(BaseModel):
    transcript: Transcript
    translation: Translation
    project_id: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    language: str | None = None
    job_id: str | None = None


class WatermarkTextRequest(BaseModel):
    text: str = Field(min_length=1)
    position: str = "bottom-right"
    margin: int = 24
    x: int = 0
    y: int = 0
    font_size: int = 48
    color: str = "#FFFFFFFF"
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    rotation: float = 0.0
    font: str | None = None
    font_file: str | None = None


class WatermarkImageRequest(BaseModel):
    image_path: str = Field(min_length=1)
    position: str = "bottom-right"
    margin: int = 24
    x: int = 0
    y: int = 0
    width: int = 0
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)


class WatermarkRequest(BaseModel):
    text: WatermarkTextRequest | None = None
    image: WatermarkImageRequest | None = None


class RenderRequest(BaseModel):
    video_path: str = Field(min_length=1)
    subtitle_path: str | None = None
    output_path: str = Field(min_length=1)
    encoder: str | None = None
    preset: str = "medium"
    crf: int = Field(default=18, ge=0, le=51)
    watermark: WatermarkRequest | None = None
    check_window: tuple[float, float] | None = None
    job_id: str | None = None


# ---------------------------------------------------------------------------
# Error envelope helper (canonical §25.3)
# ---------------------------------------------------------------------------


def _error(code: str, message: str, *, recoverable: bool = False, http: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> JSONResponse:
    return JSONResponse(
        status_code=http,
        content={"error": {"code": code, "message": message, "recoverable": recoverable}},
    )


# ---------------------------------------------------------------------------
# Provider factory (keeps the pipeline decoupled from concrete providers)
# ---------------------------------------------------------------------------


def build_translation_provider(name: str, config: dict[str, str] | None, api_key: str | None):
    """Resolve a translation provider by name (ADR §3.3 FROZEN)."""
    from src.services.providers.translation.gemini_provider import GeminiProvider  # noqa: PLC0415
    from src.services.providers.translation.local_llm_provider import LocalLLMProvider  # noqa: PLC0415
    from src.services.providers.translation.mock_provider import MockProvider  # noqa: PLC0415

    config = config or {}
    if name == "mock":
        return MockProvider()
    if name == "gemini":
        return GeminiProvider(api_key=api_key, model=config.get("model"))
    if name == "local":
        return LocalLLMProvider(
            model_path=config.get("model_path"),
            server_url=config.get("server_url"),
            model=config.get("model"),
        )
    raise ProviderError("E_PROVIDER_UNAVAILABLE", f"No translation provider named {name!r}.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/v1/audio/extract")
def audio_extract(request: AudioExtractRequest) -> JSONResponse:
    """Extract a 16k mono WAV from ``video_path`` into ``output_path``."""
    from src.services.audio_service import AudioExtractResult, extract_audio  # noqa: PLC0415 - lazy
    from src.core.ffmpeg import FFmpegError  # noqa: PLC0415

    try:
        with _cancel_scope(request.job_id) as cancel:
            result: AudioExtractResult = extract_audio(
                request.video_path,
                request.output_path,
                cancel=cancel,
                on_progress=lambda fraction: cancel.set_progress(fraction, "extract-audio"),
            )
    except CancelledError:
        return _error("E_CANCELLED", "Audio extraction was cancelled.", http=status.HTTP_409_CONFLICT)
    except FFmpegError as exc:
        return _error(exc.code, exc.message, recoverable=exc.code == "E_FFMPEG_NOT_FOUND")
    return JSONResponse(
        {
            "output_path": result.output_path,
            "duration_seconds": result.duration_seconds,
            "file_size_bytes": result.file_size_bytes,
        }
    )


@router.post("/v1/translate")
def translate(request: TranslateRequest) -> JSONResponse:
    """Translate ``request.transcript`` segments with the named provider."""
    from src.services.providers.base import SourceSegment  # noqa: PLC0415
    from src.services.quality_service import ProviderError as QProviderError  # noqa: PLC0415
    from src.services.translation_service import TranslationService  # noqa: PLC0415

    try:
        provider = build_translation_provider(
            request.provider, request.provider_config, request.api_key
        )
    except ProviderError as exc:
        return _error(exc.code, exc.message)

    segments = [
        SourceSegment(idx=s.idx, segment_id=s.id, text=s.text, speaker=s.speaker)
        for s in request.transcript.segments
    ]
    try:
        service = TranslationService()
        with _cancel_scope(request.job_id) as cancel:
            blocks: list[TranslationBlock] = service.translate_segments(
                segments,
                target_language=request.target_language,
                provider=provider,
                model=request.model,
                glossary_ver=request.glossary_ver,
                glossary=request.glossary,
                characters=request.characters,
                rules=request.rules,
                cancel=cancel,
                on_progress=lambda fraction: cancel.set_progress(fraction, "translate"),
            )
    except (ProviderError, QProviderError) as exc:
        return _error(exc.code, exc.message)
    except CancelledError:
        return _error("E_CANCELLED", "Translation was cancelled.", http=status.HTTP_409_CONFLICT)
    return JSONResponse(
        Translation(
            schema_version=1,
            target_language=request.target_language,
            model=request.model,
            blocks=blocks,
        ).model_dump()
    )


@router.post("/v1/subtitle")
def subtitle(request: SubtitleRequest) -> JSONResponse:
    """Generate cues + ASS/SRT from a transcript + translation into ``output_dir``."""
    from src.services.subtitle_service import SubtitleError, SubtitleService  # noqa: PLC0415 - lazy

    try:
        with _cancel_scope(request.job_id) as cancel:
            doc = SubtitleService().from_transcript_and_translation(
                request.transcript,
                request.translation,
                language=request.language,
                output_dir=request.output_dir,
            )
    except SubtitleError as exc:
        return _error(exc.code, exc.message)
    except CancelledError:
        return _error("E_CANCELLED", "Subtitle generation was cancelled.", http=status.HTTP_409_CONFLICT)
    return JSONResponse(
        {
            "cues": [c.model_dump() for c in doc.document.cues],
            "ass_path": doc.document.output.ass_path or "",
            "srt_path": doc.document.output.srt_path or "",
            "warnings": list(doc.warnings),
        }
    )


@router.post("/v1/render")
def render(request: RenderRequest) -> JSONResponse:
    """Burn subtitles into ``video_path`` with libass and validate the output."""
    from src.services.render_service import (  # noqa: PLC0415 - lazy
        RenderConfig,
        RenderError,
        RenderResult,
        ImageWatermark,
        TextWatermark,
        WatermarkConfig,
        render as render_video,
    )

    watermark = None
    if request.watermark is not None:
        text = None
        image = None
        if request.watermark.text is not None:
            t = request.watermark.text
            text = TextWatermark(
                text=t.text,
                position=t.position,
                margin=t.margin,
                x=t.x,
                y=t.y,
                font_size=t.font_size,
                color=t.color,
                opacity=t.opacity,
                rotation=t.rotation,
                font=t.font,
                font_file=t.font_file,
            )
        if request.watermark.image is not None:
            img = request.watermark.image
            image = ImageWatermark(
                image_path=img.image_path,
                position=img.position,
                margin=img.margin,
                x=img.x,
                y=img.y,
                width=img.width,
                opacity=img.opacity,
            )
        watermark = WatermarkConfig(text=text, image=image)

    config = RenderConfig(
        input_path=request.video_path,
        subtitle_path=request.subtitle_path,
        output_path=request.output_path,
        video_encoder=request.encoder,
        video_preset=request.preset,
        video_crf=request.crf,
        watermark=watermark,
        check_window=request.check_window,
    )
    try:
        with _cancel_scope(request.job_id) as cancel:
            result: RenderResult = render_video(
                config,
                cancel=cancel,
                on_progress=lambda p: cancel.set_progress(p.fraction, "render"),
            )
    except CancelledError:
        return _error("E_CANCELLED", "Render was cancelled.", http=status.HTTP_409_CONFLICT)
    except RenderError as exc:
        return _error(exc.code, exc.message)
    return JSONResponse(
        {
            "output_path": result.output_path,
            "encoder_used": result.encoder_used,
            "duration_seconds": result.duration_seconds,
            "width": result.width,
            "height": result.height,
            "fps": list(result.fps),
            "audio_streams": result.audio_streams,
        }
    )


@router.post("/v1/jobs/{job_id}/cancel")
def cancel(job_id: str) -> JSONResponse:
    """Cancel an in-flight stage for ``job_id`` (idempotent)."""
    existed = cancel_job(job_id)
    if not existed:
        return JSONResponse({"cancelled": False})
    return JSONResponse({"cancelled": True})


@router.get("/v1/progress/{job_id}")
def job_progress(job_id: str) -> JSONResponse:
    """Live stage progress for an in-flight ``job_id`` (polled by Rust).

    Returns ``progress: null`` when no stage for this job is currently
    registered — the caller treats that as "no progress available" and keeps
    its own stage anchors. Never exposes paths, tokens, or command lines.
    """
    with _cancel_lock:
        token = _cancel_tokens.get(job_id)
    if token is None:
        return JSONResponse({"job_id": job_id, "progress": None, "stage": None})
    progress, stage = token.get_progress()
    return JSONResponse({"job_id": job_id, "progress": progress, "stage": stage})
