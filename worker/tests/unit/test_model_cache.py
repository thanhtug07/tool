"""Unit tests for ModelCache (TASK-016D): has/ensure/import/remove semantics.

TASK-016D DoD: model verified -> không tải lại; corrupt model không dùng; xóa
model dọn sạch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.model_cache import E_MODEL_CACHE, ModelCache, ModelCacheError
from src.services.model_downloader import MODEL_FILE, import_model
from src.services.model_registry import ModelEntry, ModelRegistry
from src.services.model_verifier import STATUS_READY

PAYLOAD = b"cache fixture payload" * 50


def _entry() -> ModelEntry:
    return ModelEntry(
        id="ggml-tiny",
        name="tiny",
        version="v1",
        source="fixture",
        download_url="https://example.com/tiny",
        expected_size_bytes=len(PAYLOAD),
        checksum="",
        license="MIT",
        required_vram_mb=0.0,
        supported_backend=("whisper-cpp",),
    )


def _registry(tmp_path: Path) -> ModelRegistry:
    manifest = tmp_path / "registry.json"
    manifest.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "ggml-tiny",
                        "name": "tiny",
                        "version": "v1",
                        "source": "fixture",
                        "download_url": "https://example.com/tiny",
                        "expected_size_bytes": len(PAYLOAD),
                        "checksum": "",
                        "license": "MIT",
                        "required_vram_mb": 0,
                        "supported_backend": ["whisper-cpp"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return ModelRegistry(
        manifest_path=manifest,
        schema_path=Path(__file__).parents[3] / "schemas" / "model.schema.json",
    ).load()


def _cache(tmp_path: Path) -> ModelCache:
    return ModelCache(tmp_path / "models", _registry(tmp_path))


def _install_import(cache: ModelCache, tmp_path: Path) -> Path:
    source = tmp_path / "offline.bin"
    source.write_bytes(PAYLOAD)
    import_model(_entry(), source, cache.root)
    return source


class TestHas:
    def test_has_is_false_until_ready(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        assert cache.has("ggml-tiny") is False

    def test_has_true_after_verified_import(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        assert cache.has("ggml-tiny") is True
        assert cache.has("ggml-tiny", version="v1") is True

    def test_has_false_when_version_mismatch(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        assert cache.has("ggml-tiny", version="v2") is False

    def test_unknown_id_is_false(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        assert cache.has("nope") is False

    def test_corrupt_model_not_used(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        path = cache.file_path(_entry())
        path.write_bytes(path.read_bytes() + b"x")
        assert cache.has("ggml-tiny") is False


class TestEnsure:
    def test_ready_model_not_redownloaded(self, tmp_path, monkeypatch) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)

        def _boom(*_a, **_k):
            raise AssertionError("ensure() must not re-download a ready model")

        monkeypatch.setattr("src.services.model_cache.download_model", _boom)
        result = cache.ensure(_entry())
        assert result.status == STATUS_READY

    def test_corrupt_model_repaired(self, tmp_path, monkeypatch) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        path = cache.file_path(_entry())
        path.write_bytes(path.read_bytes() + b"!")

        def _redownload(entry, root, *, cancel=None, on_progress=None):
            import_model(entry, _write_valid(tmp_path), root)

        def _write_valid(d) -> Path:
            p = d / "valid.bin"
            p.write_bytes(PAYLOAD)
            return p

        monkeypatch.setattr("src.services.model_cache.download_model", _redownload)
        assert cache.has("ggml-tiny") is False
        result = cache.ensure(_entry())
        assert result.status == STATUS_READY
        assert cache.has("ggml-tiny") is True

    def test_failed_install_raises(self, tmp_path, monkeypatch) -> None:
        cache = _cache(tmp_path)

        def _nothing(*_a, **_k):
            return None

        monkeypatch.setattr("src.services.model_cache.download_model", _nothing)
        with pytest.raises(ModelCacheError) as excinfo:
            cache.ensure(_entry())
        assert excinfo.value.code == E_MODEL_CACHE


class TestImport:
    def test_import_counts_only_when_verified(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        source = tmp_path / "offline.bin"
        source.write_bytes(PAYLOAD)
        result = cache.import_file(_entry(), source)
        assert result.status == STATUS_READY
        assert cache.has("ggml-tiny") is True


class TestRemove:
    def test_remove_cleans_dir(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        assert cache.remove("ggml-tiny") is True
        assert not cache._dir(_entry()).exists()
        assert cache.has("ggml-tiny") is False

    def test_remove_unknown_returns_false(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        assert cache.remove("nope") is False

    def test_list_reports_statuses(self, tmp_path) -> None:
        cache = _cache(tmp_path)
        _install_import(cache, tmp_path)
        results = cache.list()
        assert {r.status for r in results} == {STATUS_READY}