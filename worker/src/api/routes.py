"""Worker HTTP routes (TASK-005, TASK-006 sidecar auth, TASK-013 STT,
TASK-029 export).

- ``GET /health`` — cheap liveness probe (no heavy imports).
- ``POST /v1/stt/transcribe`` — STT stage (faster-whisper, lazy-loaded).
- ``POST /v1/export/video`` — copy a rendered video to a user directory + QC.
- ``POST /v1/export/subtitles`` — export a subtitle file (optionally converted).
"""

import logging
import os
import secrets
import threading

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import __version__
from src.api.schemas import HealthResponse

# Placeholder token for local development (TASK-005).
# TASK-006 injects a random per-session token over stdin via
# ``configure_auth_token``; it takes precedence over the WORKER_AUTH_TOKEN
# environment override and the placeholder default.
PLACEHOLDER_TOKEN = "dev-placeholder-token"

logger = logging.getLogger(__name__)

router = APIRouter()

_token_lock = threading.Lock()
_session_token: str | None = None


def configure_auth_token(token: str | None) -> None:
    """Set the per-session bearer token received from the sidecar parent.

    ``None`` restores the dev-mode fallback chain (env → placeholder).
    """
    global _session_token
    with _token_lock:
        _session_token = token


def _expected_token() -> str:
    with _token_lock:
        configured = _session_token
    if configured:
        return configured
    return os.environ.get("WORKER_AUTH_TOKEN", PLACEHOLDER_TOKEN)


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    """Reject requests that do not carry the expected ``Authorization: Bearer`` token."""
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, _expected_token()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


@router.get("/health", response_model=HealthResponse, dependencies=[Depends(require_bearer)])
def health() -> HealthResponse:
    """Report that the worker process is alive and responding to HTTP requests.

    Deliberately free of heavy imports (torch/faster-whisper load lazily later)
    so the endpoint stays cheap and deterministic.
    """
    return HealthResponse(status="ok", version=__version__, gpu=None)


class TranscribeRequest(BaseModel):
    """Request body for ``/v1/stt/transcribe`` (request-only contract; the
    Transcript response is the canonical artifact in schemas/)."""

    audio_path: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    model: str = "large-v3"
    device: str = "auto"
    language: str | None = None
    total_duration_seconds: float | None = Field(default=None, ge=0.0)
    # Pipeline wiring (RELEASE-P0): per-job cancellation + progress logging.
    job_id: str | None = None


@router.post(
    "/v1/stt/transcribe",
    dependencies=[Depends(require_bearer)],
)
def stt_transcribe(request: TranscribeRequest) -> dict:
    """Transcribe ``audio_path`` and return a canonical Transcript document.

    STT is AI-dependent: without a downloaded model this returns a clean
    ``E_STT_*`` error, never a shell/stack-trace leak.
    """
    from src.api.pipeline import _cancel_scope  # noqa: PLC0415 - loopback cancel registry
    from src.core.job import CancelledError  # noqa: PLC0415
    from src.services.stt_service import STTError, transcribe  # noqa: PLC0415 - lazy

    try:
        with _cancel_scope(request.job_id) as cancel:
            result = transcribe(
                request.audio_path,
                project_id=request.project_id,
                model_name=request.model,
                device=request.device,
                language=request.language,
                total_duration_seconds=request.total_duration_seconds,
                cancel=cancel,
                on_progress=lambda fraction: logger.info(
                    "transcribe %.0f%%", fraction * 100
                ),
            )
    except CancelledError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": {"code": "E_CANCELLED", "message": "Transcription was cancelled.", "recoverable": False}},
        )
    except STTError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {"code": exc.code, "message": exc.message, "recoverable": False}
            },
        )
    return result.transcript


class ExportVideoRequest(BaseModel):
    """Request body for ``POST /v1/export/video`` (TASK-029)."""

    source_video: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    name: str | None = None
    run_qc: bool = True


class ExportSubtitleRequest(BaseModel):
    """Request body for ``POST /v1/export/subtitles`` (TASK-029).

    ``format`` is ``srt`` / ``vtt`` / ``ass``; ``None`` keeps the source
    extension. ASS cannot be converted to another format (MVP).
    """

    source_subtitle: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    name: str | None = None
    format: str | None = None


# Errors the user can act on (pick another directory / free disk space) are
# ``recoverable``; the rest are not. Mirrors MASTER_PLAN §25.3 error envelope.
_RECOVERABLE_EXPORT_CODES = frozenset({"E_PERMISSION_DENIED", "E_DISK_FULL"})


def _export_error_response(exc) -> JSONResponse:
    """Map a worker ``RenderError`` to the canonical error envelope."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "recoverable": exc.code in _RECOVERABLE_EXPORT_CODES,
            }
        },
    )


@router.post(
    "/v1/export/video",
    dependencies=[Depends(require_bearer)],
)
def export_video_route(request: ExportVideoRequest) -> JSONResponse:
    """Copy a rendered video into ``target_dir`` and QC it (TASK-029)."""
    from src.services.render_service import RenderError, export_video  # noqa: PLC0415 - lazy

    try:
        result = export_video(
            request.source_video,
            request.target_dir,
            name=request.name,
            run_qc=request.run_qc,
        )
    except RenderError as exc:
        return _export_error_response(exc)
    return JSONResponse(
        {
            "path": result.path,
            "qc": {
                "passed": result.qc.passed,
                "issues": list(result.qc.issues),
                "warnings": list(result.qc.warnings),
            },
        }
    )


@router.post(
    "/v1/export/subtitles",
    dependencies=[Depends(require_bearer)],
)
def export_subtitles_route(request: ExportSubtitleRequest) -> JSONResponse:
    """Export a subtitle file, optionally converting SRT↔VTT (TASK-029)."""
    from src.services.render_service import (  # noqa: PLC0415 - lazy
        RenderError,
        SubtitleExportOptions,
        export_subtitles,
    )

    try:
        path = export_subtitles(
            request.source_subtitle,
            request.target_dir,
            options=SubtitleExportOptions(format=request.format, name=request.name),
        )
    except RenderError as exc:
        return _export_error_response(exc)
    return JSONResponse({"path": path})
