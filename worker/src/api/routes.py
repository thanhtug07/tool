"""Worker HTTP routes (TASK-005, TASK-006 sidecar auth, TASK-013 STT).

- ``GET /health`` — cheap liveness probe (no heavy imports).
- ``POST /v1/stt/transcribe`` — STT stage (faster-whisper, lazy-loaded).
"""

import os
import secrets
import threading

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from src import __version__
from src.api.schemas import HealthResponse

# Placeholder token for local development (TASK-005).
# TASK-006 injects a random per-session token over stdin via
# ``configure_auth_token``; it takes precedence over the WORKER_AUTH_TOKEN
# environment override and the placeholder default.
PLACEHOLDER_TOKEN = "dev-placeholder-token"

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


@router.post(
    "/v1/stt/transcribe",
    dependencies=[Depends(require_bearer)],
)
def stt_transcribe(request: TranscribeRequest) -> dict:
    """Transcribe ``audio_path`` and return a canonical Transcript document.

    STT is AI-dependent: without a downloaded model this returns a clean
    ``E_STT_*`` error, never a shell/stack-trace leak.
    """
    from fastapi.responses import JSONResponse

    from src.services.stt_service import STTError, transcribe  # noqa: PLC0415 - lazy

    try:
        result = transcribe(
            request.audio_path,
            project_id=request.project_id,
            model_name=request.model,
            device=request.device,
            language=request.language,
            total_duration_seconds=request.total_duration_seconds,
        )
    except STTError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {"code": exc.code, "message": exc.message, "recoverable": False}
            },
        )
    return result.transcript
