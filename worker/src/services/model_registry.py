"""ModelRegistry (TASK-016A): single source of truth for model metadata.

The manifest (``models/manifest.json``) declares every downloadable model with
canonical metadata validated against ``schemas/model.schema.json``. The
registry is the only place that knows model ids, VRAM footprints and supported
backends — the downloader (TASK-016B), verifier (TASK-016C) and cache all read
from here so model names are never scattered through the codebase.

Design
------
- **Immutable identity**: each entry is addressed as ``id@version``
  (``qualified_id``); a version bump is a new entry, never an edit.
- **Schema-validated load**: entries failing ``jsonschema`` validation are
  skipped with a warning and never become available (MASTER_PLAN §32.4 /
  TASK-016A DoD "thiếu metadata -> không nằm trong list available").
- **Pinned = downloadable/verifiable**: an entry is ``available`` only when its
  checksum (64-hex) and expected size are pinned. Until TASK-016C verifies a
  download, manifest entries ship unpinned and ``resolve()`` returns nothing —
  the honest MVP state, not fabricated checksums.
- **Resolve matrix**: ``resolve(backend, vram_mb)`` returns the available
  entries for a backend that fit the free VRAM; ``best(...)`` picks the largest
  that fits (the VRAM-guard model choice).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

E_MODEL_REGISTRY = "E_MODEL_REGISTRY"

#: Backends declared by the model schema / MASTER_PLAN §14.2.
BACKEND_FASTER_WHISPER = "faster-whisper"
BACKEND_WHISPER_CPP = "whisper-cpp"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = _REPO_ROOT / "models" / "manifest.json"
DEFAULT_SCHEMA_PATH = _REPO_ROOT / "schemas" / "model.schema.json"


class ModelRegistryError(Exception):
    """Registry failure carrying the architecture error code (§28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ModelEntry:
    """One manifest entry (immutable; identity is ``id@version``)."""

    id: str
    name: str
    version: str
    source: str
    download_url: str
    expected_size_bytes: int
    checksum: str
    license: str
    required_vram_mb: float
    supported_backend: tuple[str, ...]

    @property
    def qualified_id(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def pinned(self) -> bool:
        """True when download-critical metadata is present and verifiable."""
        return bool(self.checksum) and self.expected_size_bytes > 0

    def supports(self, backend: str) -> bool:
        return backend in self.supported_backend

    def fits_vram(self, vram_mb: float | None) -> bool:
        return vram_mb is None or self.required_vram_mb == 0 or vram_mb >= self.required_vram_mb

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "download_url": self.download_url,
            "expected_size_bytes": self.expected_size_bytes,
            "checksum": self.checksum,
            "license": self.license,
            "required_vram_mb": self.required_vram_mb,
            "supported_backend": list(self.supported_backend),
        }


class ModelRegistry:
    """Loaded manifest with validation and the resolve matrix."""

    def __init__(
        self,
        manifest_path: str | Path | None = None,
        schema_path: str | Path | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path or DEFAULT_MANIFEST_PATH)
        self.schema_path = Path(schema_path or DEFAULT_SCHEMA_PATH)
        self._entries: dict[str, ModelEntry] = {}
        self._invalid: list[dict[str, str]] = []

    @property
    def invalid_entries(self) -> list[dict[str, str]]:
        return list(self._invalid)

    def load(self) -> "ModelRegistry":
        """Parse + validate the manifest; invalid entries are skipped."""
        self._entries = {}
        self._invalid = []
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelRegistryError(E_MODEL_REGISTRY, f"Cannot read the model manifest: {exc}") from exc

        models = raw.get("models") if isinstance(raw, dict) else raw
        if not isinstance(models, list):
            raise ModelRegistryError(E_MODEL_REGISTRY, "Model manifest must contain a 'models' list.")

        for item in models:
            try:
                jsonschema.validate(item, schema)
            except jsonschema.ValidationError as exc:
                model_id = item.get("id") if isinstance(item, dict) else None
                self._invalid.append({"id": str(model_id), "reason": exc.message})
                logger.warning("Model entry skipped (schema invalid): %s — %s", model_id, exc.message)
                continue
            entry = ModelEntry(
                id=item["id"],
                name=item["name"],
                version=item["version"],
                source=item["source"],
                download_url=item["download_url"],
                expected_size_bytes=item["expected_size_bytes"],
                checksum=item["checksum"],
                license=item["license"],
                required_vram_mb=float(item["required_vram_mb"]),
                supported_backend=tuple(item["supported_backend"]),
            )
            self._entries[entry.id] = entry
        return self

    def list(self) -> list[ModelEntry]:
        """All schema-valid manifest entries (any pin state), insertion order."""
        return list(self._entries.values())

    def get(self, model_id: str) -> ModelEntry | None:
        return self._entries.get(model_id)

    def resolve(
        self,
        backend: str,
        vram_mb: float | None = None,
    ) -> list[ModelEntry]:
        """Available models for ``backend`` that fit ``vram_mb``, smallest-first."""
        return sorted(
            (
                entry
                for entry in self._entries.values()
                if entry.pinned and entry.supports(backend) and entry.fits_vram(vram_mb)
            ),
            key=lambda e: e.required_vram_mb,
        )

    def best(self, backend: str, vram_mb: float | None = None) -> ModelEntry | None:
        """Largest available model that fits ``vram_mb`` (VRAM-guard choice)."""
        resolved = self.resolve(backend, vram_mb)
        return resolved[-1] if resolved else None
