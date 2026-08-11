"""ModelCache (TASK-016D): owns ``user-data/models/<id>@<version>/``.

A thin facade over the downloader (TASK-016B) + verifier (TASK-016C) + registry
(TASK-016A) that implements cache semantics:

- **``has(id, version)``** — true only when the model is present **and**
  verified ``ready`` (a corrupted model is never "there").
- **No re-download** — ``ensure()`` returns early when the model is already
  ready; only a missing/corrupt model triggers a (re)download.
- **Offline import** — ``import_file()`` copies a local model in, then runs the
  verifier so it only counts as cached when it actually verifies.
- **Clean removal** — ``remove()`` deletes the whole ``<id>@<version>`` dir
  (model file + ``.meta.json``); the manifest still lists the model, but the
  cache correctly reports it missing until re-installed.

Corrupt models are never used: ``ensure()`` clears a corrupt copy and
re-downloads, surfacing ``E_MODEL_CACHE`` if the fresh copy still fails to
verify.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.core.job import CancellationToken
from src.services.model_downloader import (
    MODEL_FILE,
    DownloadResult,
    download_model,
    import_model,
)
from src.services.model_registry import ModelEntry, ModelRegistry
from src.services.model_verifier import STATUS_READY, VerifyResult, verify_model

logger = logging.getLogger(__name__)

E_MODEL_CACHE = "E_MODEL_CACHE"


class ModelCacheError(Exception):
    """Cache failure carrying the architecture error code (§28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ModelCache:
    """Layer over the registry that owns the on-disk model cache."""

    def __init__(self, root: Path, registry: ModelRegistry) -> None:
        self.root = Path(root)
        self.registry = registry

    def _dir(self, entry: ModelEntry) -> Path:
        return self.root / f"{entry.id}@{entry.version}"

    def file_path(self, entry: ModelEntry) -> Path:
        return self._dir(entry) / MODEL_FILE

    def verify(self, entry: ModelEntry) -> VerifyResult:
        return verify_model(entry, self.root)

    def has(self, model_id: str, version: str | None = None) -> bool:
        """True only for a present, verified-ready model."""
        entry = self.registry.get(model_id)
        if entry is None:
            return False
        if version is not None and entry.version != version:
            return False
        return self.verify(entry).status == STATUS_READY

    def list(self) -> list[VerifyResult]:
        """Verification status for every manifest entry (ready/corrupt/etc.)."""
        return [self.verify(entry) for entry in self.registry.list()]

    def ensure(
        self,
        entry: ModelEntry,
        *,
        cancel: CancellationToken | None = None,
        on_progress=None,
    ) -> VerifyResult:
        """Make ``entry`` ready locally — never re-downloads a ready model."""
        current = self.verify(entry)
        if current.status == STATUS_READY:
            return current
        if current.status == "corrupt":
            logger.warning("Model %s is corrupt; clearing before re-download", entry.qualified_id)
            shutil.rmtree(self._dir(entry), ignore_errors=True)

        download_model(entry, self.root, cancel=cancel, on_progress=on_progress)
        result = self.verify(entry)
        if result.status != STATUS_READY:
            raise ModelCacheError(
                E_MODEL_CACHE,
                f"Model {entry.qualified_id} not ready after install ({result.status}).",
            )
        return result

    def import_file(self, entry: ModelEntry, source_path: Path) -> VerifyResult:
        """Register a local file, then verify — only count it when it verifies."""
        import_model(entry, source_path, self.root)
        return self.verify(entry)

    def remove(self, model_id: str) -> bool:
        """Delete the cache dir for ``model_id``; True if something was removed."""
        entry = self.registry.get(model_id)
        if entry is None:
            return False
        target = self._dir(entry)
        existed = target.exists()
        shutil.rmtree(target, ignore_errors=True)
        return existed