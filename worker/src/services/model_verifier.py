"""ModelVerifier (TASK-016C): checksum + size + license check, 3 states.

Verifies a downloaded or imported model against its recorded checksum and the
registry metadata. A model is never ``ready`` until its SHA-256 matches and its
size (when pinned) agrees — a single corrupted byte flips it to ``corrupt`` so
the app can offer re-download instead of shipping a broken model.

States (TASK-016C DoD):

- ``ready``      — checksum matches, size matches, license present.
- ``corrupt``    — file missing, size mismatch, or checksum mismatch.
- ``unverified`` — verifiable checksum is unknown (unpinned manifest / no
  ``.meta.json``), or the license is empty. Never blocks dev but never counts
  as ready.

Integrity vs. license: a checksum failure always wins and reports ``corrupt``,
even when the license is also empty; an empty license only downgrades a
checksum-OK model to ``unverified``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from src.services.model_downloader import MODEL_FILE, sha256_file
from src.services.model_registry import ModelEntry, ModelRegistry

logger = logging.getLogger(__name__)

STATUS_READY = "ready"
STATUS_CORRUPT = "corrupt"
STATUS_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class VerifyResult:
    """Verification outcome for one model."""

    model_id: str
    version: str
    status: str
    sha256: str | None
    size_bytes: int | None
    message: str


def _model_dir(cache_dir: Path, entry: ModelEntry) -> Path:
    return cache_dir / f"{entry.id}@{entry.version}"


def _read_meta(model_dir: Path) -> dict:
    meta_path = model_dir / ".meta.json"
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def verify_model(
    entry: ModelEntry,
    cache_dir: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> VerifyResult:
    """Verify ``entry`` in ``cache_dir`` against checksum/size/license."""
    model_dir = _model_dir(cache_dir, entry)
    file_path = model_dir / MODEL_FILE

    if not file_path.is_file():
        return VerifyResult(
            entry.id, entry.version, STATUS_CORRUPT, None, None,
            "Missing model file.",
        )

    meta = _read_meta(model_dir)
    size = file_path.stat().st_size
    expected = expected_sha256 or meta.get("sha256") or None
    license_ok = bool((entry.license or "").strip())

    if not expected:
        return VerifyResult(
            entry.id, entry.version, STATUS_UNVERIFIED, None, size,
            "No checksum available to verify against.",
        )

    actual = sha256_file(file_path)
    if expected_size is not None and size != expected_size:
        return VerifyResult(
            entry.id, entry.version, STATUS_CORRUPT, actual, size,
            f"Size mismatch (got {size}, expected {expected_size}).",
        )
    if actual != expected:
        return VerifyResult(
            entry.id, entry.version, STATUS_CORRUPT, actual, size,
            "Checksum mismatch (corrupted or forged file).",
        )
    if not license_ok:
        return VerifyResult(
            entry.id, entry.version, STATUS_UNVERIFIED, actual, size,
            "Checksum ok, but the model license is empty/unverified.",
        )
    return VerifyResult(
        entry.id, entry.version, STATUS_READY, actual, size,
        "Model verified (checksum + size + license).",
    )


def ready_models(registry: ModelRegistry, cache_dir: Path) -> list[VerifyResult]:
    """Only ``ready`` models — the ``available`` set for the pipeline."""
    return [
        result
        for entry in registry.list()
        if (result := verify_model(entry, cache_dir)).status == STATUS_READY
    ]