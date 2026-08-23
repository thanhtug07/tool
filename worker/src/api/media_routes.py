"""Media & Artifact Serving Routes (Phase 9 & 10).

Provides range-request capable file serving for videos, audio tracks, and previews,
replacing raw OS file paths and Tauri asset protocol with backend HTTP URLs.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, status
from fastapi.responses import FileResponse, StreamingResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

@router.get("/stream")
def stream_media(path: str, range_header: Optional[str] = Header(None, alias="Range")):
    """Stream a local media file with HTTP 206 Partial Content range support for video/audio seeking.

    The worker runs on localhost only (127.0.0.1) so arbitrary path access is
    acceptable - there is no remote attack surface.
    """
    file_path = Path(path).resolve()

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {path}",
        )

    file_size = file_path.stat().st_size
    content_type = "video/mp4"
    if file_path.suffix.lower() in (".wav", ".mp3", ".m4a"):
        content_type = "audio/wav"

    if not range_header:
        return FileResponse(file_path, media_type=content_type)

    # Parse Range header e.g. "bytes=0-1024"
    try:
        unit, _, ranges = range_header.partition("=")
        if unit.strip().lower() != "bytes":
            return FileResponse(file_path, media_type=content_type)

        start_str, _, end_str = ranges.partition("-")
        start = int(start_str.strip()) if start_str.strip() else 0
        end = int(end_str.strip()) if end_str.strip() else file_size - 1
        end = min(end, file_size - 1)

        if start > end or start >= file_size:
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        content_length = end - start + 1

        def _iter_file(chunk_size=64 * 1024):
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    read_bytes = min(chunk_size, remaining)
                    data = f.read(read_bytes)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
        }

        return StreamingResponse(
            _iter_file(),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=content_type,
            headers=headers,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to parse Range header or stream file %s: %s", path, exc)
        return FileResponse(file_path, media_type=content_type)



@router.get("/probe")
def probe_media(path: str) -> dict:
    """Run ffprobe on a local media file and return structured metadata.

    Returns the same shape the frontend MediaProbe type expects:
    duration, width, height, fps, audioTracks, videoCodec, container.
    """
    import json as _json
    import subprocess

    file_path = Path(path).resolve()
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {path}",
        )

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"ffprobe failed: {result.stderr[:200]}",
            )
        data = _json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ffprobe timed out",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ffprobe error: {exc}",
        )

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    duration = float(fmt.get("duration", 0))
    container = fmt.get("format_name")

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    width = video_streams[0].get("width") if video_streams else None
    height = video_streams[0].get("height") if video_streams else None
    video_codec = video_streams[0].get("codec_name") if video_streams else None

    fps = None
    if video_streams:
        r_frame_rate = video_streams[0].get("r_frame_rate", "0/1")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/", 1)
            try:
                fps = round(int(num) / int(den), 2) if int(den) else None
            except (ValueError, ZeroDivisionError):
                fps = None
        else:
            try:
                fps = float(r_frame_rate)
            except ValueError:
                fps = None

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "audioTracks": len(audio_streams),
        "videoCodec": video_codec,
        "container": container,
    }
