"""Unit tests for ModelRegistry (TASK-016A): manifest parse, validation,
resolve matrix (backend + VRAM), and pin/availability semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.model_registry import (
    BACKEND_FASTER_WHISPER,
    BACKEND_WHISPER_CPP,
    DEFAULT_MANIFEST_PATH,
    E_MODEL_REGISTRY,
    ModelRegistry,
    ModelRegistryError,
)

SHA = "a" * 64


def _entry(model_id, *, vram, backends, size=1000, checksum=SHA):
    return {
        "id": model_id,
        "name": f"Model {model_id}",
        "version": "v1",
        "source": "fixture",
        "download_url": f"https://example.com/models/{model_id}",
        "expected_size_bytes": size,
        "checksum": checksum,
        "license": "MIT",
        "required_vram_mb": vram,
        "supported_backend": backends,
    }


def _write_manifest(tmp_path, models):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    return path


def _registry(tmp_path, models, schema=DEFAULT_MANIFEST_PATH.parent.parent / "schemas" / "model.schema.json"):
    return ModelRegistry(manifest_path=_write_manifest(tmp_path, models), schema_path=schema).load()


@pytest.fixture
def manifest_schema():
    return Path(__file__).resolve().parents[3] / "schemas" / "model.schema.json"


class TestManifestParse:
    def test_load_lists_in_order(self, tmp_path) -> None:
        registry = _registry(tmp_path, [_entry("m-a", vram=400, backends=[BACKEND_FASTER_WHISPER])])
        assert len(registry.list()) == 1
        assert registry.list()[0].id == "m-a"
        assert registry.list()[0].qualified_id == "m-a@v1"

    def test_get_by_id(self, tmp_path) -> None:
        registry = _registry(
            tmp_path,
            [_entry("m-a", vram=400, backends=[BACKEND_FASTER_WHISPER])],
        )
        assert registry.get("m-a") is not None
        assert registry.get("missing") is None

    def test_shipped_manifest_is_valid(self, manifest_schema) -> None:
        registry = ModelRegistry(schema_path=manifest_schema).load()
        assert len(registry.list()) >= 5
        assert registry.invalid_entries == []

    def test_missing_manifest_raises(self, tmp_path) -> None:
        registry = ModelRegistry(
            manifest_path=tmp_path / "nope.json",
            schema_path=tmp_path / "schemas",
        )
        with pytest.raises(ModelRegistryError) as excinfo:
            registry.load()
        assert excinfo.value.code == E_MODEL_REGISTRY


class TestValidation:
    def test_invalid_entry_skipped_and_reported(self, tmp_path) -> None:
        good = _entry("good", vram=400, backends=[BACKEND_FASTER_WHISPER])
        bad = dict(good)
        bad["id"] = "BAD ID!"  # violates ^[a-z0-9...] pattern
        registry = _registry(tmp_path, [good, bad])
        assert [e.id for e in registry.list()] == ["good"]
        assert registry.invalid_entries[0]["id"] == "BAD ID!"

    def test_missing_required_field_skipped(self, tmp_path) -> None:
        entry = _entry("m-a", vram=400, backends=[BACKEND_FASTER_WHISPER])
        del entry["license"]
        registry = _registry(tmp_path, [entry])
        assert registry.list() == []
        assert len(registry.invalid_entries) == 1


class TestAvailability:
    def test_unpinned_entries_never_resolve(self, tmp_path) -> None:
        entry = _entry("m-a", vram=400, backends=[BACKEND_FASTER_WHISPER], checksum="")
        registry = _registry(tmp_path, [entry])
        assert registry.get("m-a") is not None  # listed but...
        assert registry.resolve(BACKEND_FASTER_WHISPER) == []  # ...not available

    def test_pinned_entries_resolve(self, tmp_path) -> None:
        entry = _entry("m-a", vram=400, backends=[BACKEND_FASTER_WHISPER])
        registry = _registry(tmp_path, [entry])
        resolved = registry.resolve(BACKEND_FASTER_WHISPER)
        assert [e.id for e in resolved] == ["m-a"]


class TestResolveMatrix:
    def test_backend_filter(self, tmp_path) -> None:
        models = [
            _entry("fw", vram=400, backends=[BACKEND_FASTER_WHISPER]),
            _entry("cpp", vram=400, backends=[BACKEND_WHISPER_CPP]),
        ]
        registry = _registry(tmp_path, models)
        assert [e.id for e in registry.resolve(BACKEND_FASTER_WHISPER)] == ["fw"]
        assert [e.id for e in registry.resolve(BACKEND_WHISPER_CPP)] == ["cpp"]

    def test_vram_filter_and_ordering(self, tmp_path) -> None:
        models = [
            _entry("large", vram=2900, backends=[BACKEND_FASTER_WHISPER]),
            _entry("tiny", vram=400, backends=[BACKEND_FASTER_WHISPER]),
            _entry("small", vram=1200, backends=[BACKEND_FASTER_WHISPER]),
        ]
        registry = _registry(tmp_path, models)
        resolved = registry.resolve(BACKEND_FASTER_WHISPER, vram_mb=1500.0)
        assert [e.id for e in resolved] == ["tiny", "small"]

    def test_vram_unknown_returns_all(self, tmp_path) -> None:
        models = [_entry("a", vram=2900, backends=[BACKEND_FASTER_WHISPER])]
        registry = _registry(tmp_path, models)
        assert len(registry.resolve(BACKEND_FASTER_WHISPER, vram_mb=None)) == 1

    def test_best_returns_largest_that_fits(self, tmp_path) -> None:
        models = [
            _entry("large", vram=2900, backends=[BACKEND_FASTER_WHISPER]),
            _entry("small", vram=1200, backends=[BACKEND_FASTER_WHISPER]),
            _entry("tiny", vram=400, backends=[BACKEND_FASTER_WHISPER]),
        ]
        registry = _registry(tmp_path, models)
        assert registry.best(BACKEND_FASTER_WHISPER, vram_mb=2000.0).id == "small"
        assert registry.best(BACKEND_FASTER_WHISPER, vram_mb=99999.0).id == "large"
        assert registry.best(BACKEND_FASTER_WHISPER, vram_mb=100.0) is None

    def test_zero_vram_cpu_only_always_fits(self, tmp_path) -> None:
        entry = _entry("ggml", vram=0, backends=[BACKEND_WHISPER_CPP])
        registry = _registry(tmp_path, [entry])
        assert len(registry.resolve(BACKEND_WHISPER_CPP, vram_mb=1.0)) == 1
