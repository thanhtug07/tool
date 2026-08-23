"""Settings + public TTS voices HTTP routes (web-mode parity for Tauri commands).

- GET  /api/settings        → full settings snapshot
- POST /api/settings        → set one key, returns updated snapshot
- GET  /api/tts/voices      → public (no auth) TTS voice list
- POST /api/tts/preview     → public TTS preview synthesis
"""

from __future__ import annotations

import hashlib
import logging
import wave
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.core.db import get_all_settings, set_setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsSetRequest(BaseModel):
    key: str = Field(min_length=1)
    value: str


@router.get("/settings")
def get_settings() -> dict:
    """Return all known settings (stored value or default)."""
    return get_all_settings()


@router.post("/settings")
def update_setting(request: SettingsSetRequest) -> dict:
    """Validate and persist one setting; returns the updated snapshot."""
    try:
        return set_setting(request.key, request.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# Public TTS voices endpoint (no auth required for web-mode voice picker)
# ---------------------------------------------------------------------------


@router.get("/tts/voices")
def public_tts_voices() -> dict:
    """Available TTS voices per engine — public endpoint (no bearer auth).

    Mirrors the authenticated /v1/tts/voices but without the bearer token
    requirement so the browser can call it directly.
    """
    from src.services.tts_service import (
        EDGE_VOICES,
        PIPER_VOICES,
        _DEFAULT_VOICE_FALLBACK,
        available_engines,
        voice_meta,
    )

    installed = set(available_engines())
    engines = []
    for engine_id, label, voices in (
        ("edge", "Edge (cloud — Microsoft neural, best quality)", EDGE_VOICES),
        ("piper", "Piper (local — offline, lower quality)", PIPER_VOICES),
    ):
        engines.append(
            {
                "id": engine_id,
                "label": label,
                "available": engine_id in installed,
                "voices": [
                    {"id": vid, "label": vlabel, **voice_meta(engine_id, vid)}
                    for vid, vlabel in voices.items()
                ],
            }
        )
    return {
        "engines": engines,
        "defaults": {
            engine: {"voice": voice}
            for engine, voice in _DEFAULT_VOICE_FALLBACK.items()
        },
    }


class TTSPreviewRequest(BaseModel):
    engine: str = "edge"
    voice: str = Field(min_length=1)
    text: str = Field(min_length=1)
    output_dir: str | None = None


@router.post("/tts/preview")
def public_tts_preview(request: TTSPreviewRequest) -> dict:
    """Synthesize one short clip for a voice preview (public, no auth)."""
    import tempfile

    from src.services.tts_service import TTSError, synthesize_preview

    cache_dir = (
        Path(request.output_dir)
        if request.output_dir
        else Path(tempfile.gettempdir()) / "aivideo-tts-preview"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{request.engine}-{request.voice}-{hashlib.sha1(request.text.encode('utf-8')).hexdigest()[:12]}"
    out = cache_dir / f"{key}.wav"
    if out.is_file():
        try:
            with wave.open(str(out), "rb") as w:
                duration = w.getnframes() / float(w.getframerate())
            return {"path": str(out), "duration_seconds": round(duration, 3), "cached": True}
        except Exception:
            out.unlink(missing_ok=True)
    try:
        duration = synthesize_preview(
            request.voice,
            engine=request.engine,
            text=request.text,
            out_wav=str(out),
        )
    except TTSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )
    return {"path": str(out), "duration_seconds": round(duration, 3), "cached": False}
