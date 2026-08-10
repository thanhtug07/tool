"""CacheService Python side (TASK-011).

Mirrors the Rust ``CacheService`` key builders from ARCHITECTURE_DECISION.md §3.7
and provides file-level cache access for worker stages. The Rust core owns the
authoritative index (SQLite ``cache_entries``) and the quota LRU eviction; this
module deliberately never touches the database — worker stages only read/write
payload files under a cache root, named ``{stage}_{sha256(key)}`` exactly as the
Rust service names them, so both sides address the same files.

Key formats (FROZEN, must stay byte-for-byte identical with the Rust side):

- audio  ``audio:{sha256(video)}:{spec}``
- stt    ``stt:{sha256(audio)}:{model}:{compute}:{lang}:{vad}``
- tr     ``tr:{sha256(source)}:{target}:{model}:{glossary_ver}:{rules_ver}``
- render ``render:{sha256(video|style|wm|encoder|preset)}`` (``|`` separator!)

Security model: paths are always built from key digests (hex), never from user
input; no key component can escape the cache root.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Stage order; downstream invalidation on the Rust side removes everything at
# and after the given stage. Must match `src-tauri/.../cache_service.rs`.
STAGE_ORDER: tuple[str, ...] = ("audio", "stt", "tr", "subtitle", "render")


def sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """SHA-256 hex digest of a file, streamed (never loads the file into RAM)."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def audio_key(video_sha256: str, spec: str) -> str:
    return f"audio:{video_sha256}:{spec}"


def stt_key(audio_sha256: str, model: str, compute: str, lang: str, vad: str) -> str:
    return f"stt:{audio_sha256}:{model}:{compute}:{lang}:{vad}"


def tr_key(source_sha256: str, target: str, model: str, glossary_ver: str, rules_ver: str) -> str:
    return f"tr:{source_sha256}:{target}:{model}:{glossary_ver}:{rules_ver}"


def render_key(video_sha256: str, style: str, watermark: str, encoder: str, preset: str) -> str:
    folded = "|".join((video_sha256, style, watermark, encoder, preset))
    return f"render:{sha256_hex(folded.encode('utf-8'))}"


def _validate_stage(stage: str) -> str:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown cache stage: {stage!r}")
    return stage


class CacheDir:
    """File-level cache access used by worker stages.

    Layout matches the Rust service: ``{root}/{stage}_{sha256(key)}``. Writes are
    atomic (temp file + ``os.replace``), so a crash mid-write never leaves a
    partially-readable payload at the final name.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, key: str, stage: str) -> Path:
        _validate_stage(stage)
        return self.root / f"{stage}_{sha256_hex(key.encode('utf-8'))}"

    def get(self, key: str, stage: str) -> Path | None:
        path = self.path_for(key, stage)
        return path if path.is_file() else None

    def set(self, key: str, stage: str, data: bytes) -> Path:
        path = self.path_for(key, stage)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        return path

    def set_from_path(self, key: str, stage: str, src: str | Path) -> Path:
        path = self.path_for(key, stage)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        with open(src, "rb") as src_handle, open(tmp, "wb") as tmp_handle:
            for chunk in iter(lambda: src_handle.read(64 * 1024), b""):
                tmp_handle.write(chunk)
        os.replace(tmp, path)
        return path

    def delete(self, key: str, stage: str) -> bool:
        path = self.path_for(key, stage)
        if not path.is_file():
            return False
        os.remove(path)
        return True

    def total_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(entry.stat().st_size for entry in self.root.iterdir() if entry.is_file())
