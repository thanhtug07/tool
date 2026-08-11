"""Unit tests for ModelVerifier (TASK-016C): 3 states, checksum/size/license.

TASK-016C DoD: file sửa 1 byte -> corrupt; thiếu file -> corrupt; license trống
-> unverified; chỉ ``ready`` mới xuất hiện trong available.
"""

from __future__ import annotations

from pathlib import Path

from src.services.model_downloader import MODEL_FILE, import_model, sha256_file
from src.services.model_registry import ModelEntry, ModelRegistry
from src.services.model_verifier import (
    STATUS_CORRUPT,
    STATUS_READY,
    STATUS_UNVERIFIED,
    ready_models,
    verify_model,
)

PAYLOAD = b"model weights payload" * 100  # 2.2 KiB


def _entry(license="MIT") -> ModelEntry:
    return ModelEntry(
        id="ggml-tiny",
        name="tiny",
        version="v1",
        source="fixture",
        download_url="http://placeholder",
        expected_size_bytes=len(PAYLOAD),
        checksum="",
        license=license,
        required_vram_mb=0.0,
        supported_backend=("whisper-cpp",),
    )


def _installed(tmp_path: Path) -> Path:
    """Import the fixture so the cache dir + .meta.json are in place."""
    source = tmp_path / "src.bin"
    source.write_bytes(PAYLOAD)
    import_model(_entry(), source, tmp_path)
    return tmp_path


class TestVerifyModel:
    def test_ready(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        result = verify_model(_entry(), cache)
        assert result.status == STATUS_READY
        assert result.sha256 == sha256_file(cache / "ggml-tiny@v1" / MODEL_FILE)

    def test_one_byte_change_is_corrupt(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        path = cache / "ggml-tiny@v1" / MODEL_FILE
        data = bytearray(path.read_bytes())
        data[10] ^= 0xFF
        path.write_bytes(bytes(data))
        result = verify_model(_entry(), cache)
        assert result.status == STATUS_CORRUPT
        assert "checksum mismatch" in result.message.lower()

    def test_missing_file_is_corrupt(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        (cache / "ggml-tiny@v1" / MODEL_FILE).unlink()
        result = verify_model(_entry(), cache)
        assert result.status == STATUS_CORRUPT

    def test_size_mismatch_is_corrupt(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        result = verify_model(_entry(), cache, expected_size=len(PAYLOAD) + 1)
        assert result.status == STATUS_CORRUPT

    def test_checksum_wins_over_empty_license(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        path = cache / "ggml-tiny@v1" / MODEL_FILE
        path.write_bytes(path.read_bytes() + b"X")
        result = verify_model(_entry(license="   "), cache)
        assert result.status == STATUS_CORRUPT

    def test_empty_license_is_unverified(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        result = verify_model(_entry(license="  "), cache)
        assert result.status == STATUS_UNVERIFIED

    def test_missing_checksum_is_unverified(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        meta = cache / "ggml-tiny@v1" / ".meta.json"
        meta.write_text("{}", encoding="utf-8")
        result = verify_model(_entry(), cache)
        assert result.status == STATUS_UNVERIFIED


class TestReadyModels:
    def test_only_ready_lists_available(self, tmp_path) -> None:
        cache = _installed(tmp_path)
        registry = ModelRegistry(
            manifest_path=_write_registry_manifest(tmp_path),
            schema_path=Path(__file__).parents[3] / "schemas" / "model.schema.json",
        ).load()
        ready = ready_models(registry, cache)
        assert [r.model_id for r in ready] == ["ggml-tiny"]

        # Corrupt it -> not available any more.
        path = cache / "ggml-tiny@v1" / MODEL_FILE
        path.write_bytes(path.read_bytes() + b"!")
        assert ready_models(registry, cache) == []


def _write_registry_manifest(tmp_path: Path) -> Path:
    import json

    path = tmp_path / "registry.json"
    path.write_text(
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
    return path