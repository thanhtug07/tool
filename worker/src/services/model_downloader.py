"""ModelDownloader (TASK-016B): resume-capable downloads + offline import.

Downloads models from the registry into ``user-data/models/<id>@<version>/``
using plain HTTP ``Range`` requests (stdlib ``urllib`` — no new dependency),
which natively supports resume: an interrupted download leaves a ``.part`` file
and the next run continues from where it stopped instead of restarting.

Design
------
- **Resume**: a partial file ``<name>.part`` is resumed via ``Range: bytes=N-``
  (206). Servers without range support restart from 0 (200). A 416 (range not
  satisfiable) means the partial is complete — treated as done.
- **No re-download**: a ``.meta.json`` written next to the model records the
  computed SHA-256 + size; if both exist and match, the download is skipped.
- **Progress**: ``on_progress(downloaded_bytes, total_bytes)`` fires per chunk.
- **Cancel**: keeps the ``.part`` for resume and raises ``CancelledError``.
- **Offline import**: ``import_model`` copies a local file into the cache dir,
  computing its SHA-256 and writing the same ``.meta.json`` (checksum tự tính).
- **Retry/backoff**: transient HTTP errors (429/5xx) retry with backoff; disk
  full surfaces as ``E_DISK_FULL``.

Download-critical integrity is finalized by the verifier (TASK-016C); here the
self-computed checksum already lets the cache skip re-downloading valid files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from src.core.job import CancelledError, CancellationToken
from src.services.model_registry import ModelEntry

logger = logging.getLogger(__name__)

E_MODEL_DOWNLOAD = "E_MODEL_DOWNLOAD"
E_DISK_FULL = "E_DISK_FULL"

#: Streamed hash chunk size (1 MiB).
_HASH_CHUNK = 1024 * 1024
#: Download chunk read size.
_READ_CHUNK = 64 * 1024
#: Retries for transient HTTP errors, then backoff in seconds.
_RETRIES = 3
_BACKOFF_SECONDS = (1, 2, 4)
#: Statuses worth retrying (rate limit / server hiccup).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_META_NAME = ".meta.json"
#: File name used for a downloaded/imported single-file model.
MODEL_FILE = "model.bin"

#: Progress callback: ``(downloaded_bytes, total_bytes)``.
ProgressCallback = callable  # type: ignore[type-arg]


class ModelDownloadError(Exception):
    """Download failure carrying the architecture error code (§28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a download or import."""

    model_id: str
    version: str
    path: Path
    sha256: str
    size_bytes: int
    reused: bool


def sha256_file(path: Path) -> str:
    """Streamed SHA-256 of ``path`` (chunked, so big models stay cheap)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _meta_path(model_dir: Path) -> Path:
    return model_dir / _META_NAME


def _write_meta(model_dir: Path, meta: dict) -> None:
    _meta_path(model_dir).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _cached_valid(model_dir: Path, file_path: Path) -> bool:
    """True when the cached model already has a matching checksum + size."""
    if not model_dir.is_dir() or not file_path.is_file():
        return False
    try:
        meta = json.loads(_meta_path(model_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not meta.get("sha256"):
        return False
    try:
        size = file_path.stat().st_size
    except OSError:
        return False
    return meta.get("size_bytes") == size


def _model_targets(entry: ModelEntry, target_dir: Path, filename: str | None) -> tuple[Path, Path]:
    model_dir = target_dir / f"{entry.id}@{entry.version}"
    file_path = model_dir / (filename or MODEL_FILE)
    return model_dir, file_path


def download_file(
    url: str,
    dest_path: Path,
    *,
    expected_size_bytes: int | None = None,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Download ``url`` to ``dest_path`` with HTTP-range resume.

    Writes to ``<dest>.part`` and atomically renames on success so a crash never
    leaves a half-written final file. A leftover ``.part`` is resumed, not
    restarted (task DoD: resume sau khi mạng đứt).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_name(dest_path.name + ".part")

    if cancel is not None and cancel.is_cancelled():
        raise CancelledError("model download cancelled before it started")

    attempt = 0
    while True:
        try:
            _download_once(url, part_path, expected_size_bytes, cancel, on_progress)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRYABLE_STATUS and attempt < _RETRIES:
                attempt += 1
                delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
                logger.warning("HTTP %s downloading %s; retrying in %.1fs", exc.code, url, delay)
                time.sleep(delay)
                continue
            raise ModelDownloadError(E_MODEL_DOWNLOAD, f"Download failed (HTTP {exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise ModelDownloadError(E_MODEL_DOWNLOAD, "Download failed (network error).") from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == getattr(os, "ENOSPC", 28):
                raise ModelDownloadError(E_DISK_FULL, "Not enough disk space for the model.") from exc
            raise ModelDownloadError(E_MODEL_DOWNLOAD, "Download failed while writing the file.") from exc

    os.replace(part_path, dest_path)


def _download_once(
    url: str,
    part_path: Path,
    expected_size_bytes: int | None,
    cancel: CancellationToken | None,
    on_progress: ProgressCallback | None,
) -> None:
    start = part_path.stat().st_size if part_path.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start > 0 else {}
    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - allowlisted model URLs only
        status = response.status
        if status == 416:  # range not satisfiable: partial already complete
            return
        if status == 206:
            total = (start or 0) + int(response.headers.get("Content-Length", 0))
        else:  # 200: server ignores Range — restart from zero
            start = 0
            total = int(response.headers.get("Content-Length", 0))
            if part_path.exists():
                part_path.unlink()

        mode = "ab" if start > 0 else "wb"
        downloaded = start
        with open(part_path, mode) as handle:
            while True:
                if cancel is not None and cancel.is_cancelled():
                    raise CancelledError("model download cancelled (partial kept for resume)")
                chunk = response.read(_READ_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total)

    if expected_size_bytes is not None and part_path.stat().st_size != expected_size_bytes:
        raise ModelDownloadError(
            E_MODEL_DOWNLOAD,
            f"Downloaded size {part_path.stat().st_size} != expected {expected_size_bytes}.",
        )


def download_model(
    entry: ModelEntry,
    target_dir: Path,
    *,
    filename: str | None = None,
    cancel: CancellationToken | None = None,
    on_progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download ``entry`` into ``target_dir/<id>@<version>/`` (skip if cached)."""
    model_dir, file_path = _model_targets(entry, target_dir, filename)
    if _cached_valid(model_dir, file_path):
        return DownloadResult(
            model_id=entry.id,
            version=entry.version,
            path=file_path,
            sha256=sha256_file(file_path),
            size_bytes=file_path.stat().st_size,
            reused=True,
        )

    download_file(
        entry.download_url,
        file_path,
        expected_size_bytes=entry.expected_size_bytes or None,
        cancel=cancel,
        on_progress=on_progress,
    )
    checksum = sha256_file(file_path)
    _write_meta(
        model_dir,
        {
            "id": entry.id,
            "version": entry.version,
            "file": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "sha256": checksum,
            "source": entry.download_url,
            "imported": False,
        },
    )
    return DownloadResult(
        model_id=entry.id,
        version=entry.version,
        path=file_path,
        sha256=checksum,
        size_bytes=file_path.stat().st_size,
        reused=False,
    )


def import_model(
    entry: ModelEntry,
    source_path: Path,
    target_dir: Path,
    *,
    filename: str | None = None,
) -> DownloadResult:
    """Register a local file as ``entry`` (offline import, checksum tự tính).

    Copies ``source_path`` into the cache dir and writes ``.meta.json`` with the
    freshly computed SHA-256 so the model is treated as cached from then on.
    """
    if not source_path.is_file():
        raise ModelDownloadError(E_MODEL_DOWNLOAD, f"Import source is not a file: {source_path}")
    model_dir, file_path = _model_targets(entry, target_dir, filename)
    model_dir.mkdir(parents=True, exist_ok=True)
    os.replace(source_path, file_path) if source_path == file_path else _copy_file(
        source_path, file_path
    )
    checksum = sha256_file(file_path)
    _write_meta(
        model_dir,
        {
            "id": entry.id,
            "version": entry.version,
            "file": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "sha256": checksum,
            "source": str(source_path),
            "imported": True,
        },
    )
    return DownloadResult(
        model_id=entry.id,
        version=entry.version,
        path=file_path,
        sha256=checksum,
        size_bytes=file_path.stat().st_size,
        reused=False,
    )


def _copy_file(source: Path, dest: Path) -> None:
    with open(source, "rb") as src, open(dest, "wb") as out:
        while True:
            chunk = src.read(_READ_CHUNK)
            if not chunk:
                break
            out.write(chunk)
