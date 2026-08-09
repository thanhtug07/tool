"""Worker HTTP routes (TASK-005).

Only ``GET /health`` exists. No job/media/AI endpoints yet.
"""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from src import __version__
from src.api.schemas import HealthResponse

# Placeholder token for local development (TASK-005).
# TASK-006 will inject a random per-session token over stdin; it replaces this
# default via the WORKER_AUTH_TOKEN environment variable.
PLACEHOLDER_TOKEN = "dev-placeholder-token"

router = APIRouter()


def _expected_token() -> str:
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
