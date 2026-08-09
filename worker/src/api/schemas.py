"""API schemas for the worker HTTP surface (TASK-005)."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Deterministic health payload returned by ``GET /health``.

    ``gpu`` is always ``null`` until a real hardware probe exists (later task).
    """

    status: Literal["ok"]
    version: str
    gpu: None
