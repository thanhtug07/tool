"""Unit tests for the worker-side cache helpers (TASK-011).

The Rust ``CacheService`` owns the authoritative index and quota LRU; this
module tests the mirror key builders (parity with the Rust side via fixed
digests) and the file-level ``CacheDir`` access used by worker stages.
"""

from __future__ import annotations

import pytest

from src.services.cache import (
    STAGE_ORDER,
    CacheDir,
    audio_key,
    render_key,
    sha256_file,
    sha256_hex,
    stt_key,
    tr_key,
)


class TestSha256:
    def test_known_vectors(self) -> None:
        assert sha256_hex(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        assert sha256_hex(b"abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        assert sha256_hex(b"hello world") == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )
        assert sha256_hex(b"x" * 65) == (
            "9537c5fdf120482f7d58d25e9ed583f52c02b4e304ea814db1633ad565aed7e9"
        )

    def test_file_digest_matches_content(self, tmp_path) -> None:
        path = tmp_path / "big.bin"
        path.write_bytes(b"z" * 4097)
        assert sha256_file(path) == sha256_hex(b"z" * 4097)


class TestKeyBuilders:
    def test_formats_match_frozen_contract(self) -> None:
        assert audio_key("abc123", "wav:16000:mono") == "audio:abc123:wav:16000:mono"
        assert stt_key("deadbeef", "large-v3", "int8", "zh", "silero") == (
            "stt:deadbeef:large-v3:int8:zh:silero"
        )
        assert tr_key("feed", "vi", "gemini-2.5-flash-lite", "g3", "r2") == (
            "tr:feed:vi:gemini-2.5-flash-lite:g3:r2"
        )

    def test_render_key_matches_rust_fixed_digest(self) -> None:
        # Fixed digest — must match the Rust cache_service parity test.
        assert render_key("vid", "styleA", "wm1", "libx264", "fast") == (
            "render:f8fee54b8677570e6e2347080670aa9c9397051dde998f8951c30bfb1bad29f8"
        )
        assert render_key("vid", "styleB", "wm1", "libx264", "fast") == (
            "render:1ee25ed577d1d47b02f042d13ac943479088ec9b334c616a271f0c1337d2fa94"
        )

    def test_keys_are_stable_and_param_sensitive(self) -> None:
        assert render_key("v", "s", "wm", "e", "p") == render_key("v", "s", "wm", "e", "p")
        assert render_key("v", "sA", "wm", "e", "p") != render_key("v", "sB", "wm", "e", "p")
        assert stt_key("h", "m", "c", "l", "v") != stt_key("h", "m", "c", "l", "v2")


class TestCacheDir:
    def test_set_get_roundtrip(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        key = audio_key("abc123", "wav:16000:mono")
        path = cache.set(key, "audio", b"wavdata")
        assert path.is_file()
        assert path.read_bytes() == b"wavdata"
        assert cache.get(key, "audio") == path

    def test_miss_returns_none(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        assert cache.get("audio:sha:spec", "audio") is None

    def test_file_name_is_stage_digest_not_raw_key(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        key = "audio:abc123:wav:16000:mono"
        path = cache.set(key, "audio", b"x")
        # A Windows-illegal ':' in the key must never appear in the file name.
        assert ":" not in path.name
        assert path.name == f"audio_{sha256_hex(key.encode('utf-8'))}"

    def test_set_from_path_copies(self, tmp_path) -> None:
        src = tmp_path / "render.mp4"
        src.write_bytes(b"render-bytes")
        cache = CacheDir(tmp_path / "cache")
        dest = cache.set_from_path("render:abcd", "render", src)
        assert dest.read_bytes() == b"render-bytes"
        assert src.read_bytes() == b"render-bytes", "source untouched"

    def test_delete_and_total_bytes(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        cache.set("tr:a", "tr", b"12345")
        cache.set("tr:b", "tr", b"123")
        assert cache.total_bytes() == 8
        assert cache.delete("tr:a", "tr") is True
        assert cache.delete("tr:a", "tr") is False
        assert cache.total_bytes() == 3

    def test_set_overwrites_atomically(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        key = "tr:s"
        cache.set(key, "tr", b"old")
        cache.set(key, "tr", b"new-value")
        assert cache.get(key, "tr").read_bytes() == b"new-value"

    def test_unknown_stage_is_rejected(self, tmp_path) -> None:
        cache = CacheDir(tmp_path)
        with pytest.raises(ValueError):
            cache.set("tr:x", "bogus", b"x")
        with pytest.raises(ValueError):
            cache.path_for("tr:x", "bogus")

    def test_stage_order_matches_pipeline(self) -> None:
        assert STAGE_ORDER == ("audio", "stt", "tr", "subtitle", "render")
