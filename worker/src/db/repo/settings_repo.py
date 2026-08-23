"""Settings repository for whitelisted app-level configuration."""

import sqlite3
from typing import Dict, Any, Tuple


MIN_CACHE_QUOTA_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_CACHE_QUOTA_BYTES = 1024 * 1024 * 1024 * 1024  # 1 TB
DEFAULT_CACHE_QUOTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

SETTINGS_KEYS = (
    "ai.model",
    "ai.device",
    "ai.preset",
    "gpu.override",
    "api.gemini.base_url",
    "api.gemini.model",
    "api.local.base_url",
    "cache.quota_bytes",
    "privacy.mode",
    "privacy.telemetry",
    "tts.engine",
    "tts.voice",
    "automation.chunked",
    "automation.chunk_duration",
    "automation.chunk_overlap",
    "automation.chunk_concurrency",
    "automation.chunk_retries",
    "automation.stt_mode",
    "automation.stt_batch_size",
    "automation.orchestrator_v2",
)


def get_defaults() -> Dict[str, str]:
    return {
        "ai.model": "large-v3",
        "ai.device": "auto",
        "ai.preset": "balanced",
        "gpu.override": "auto",
        "api.gemini.base_url": "",
        "api.gemini.model": "gemini-flash-lite-latest",
        "api.local.base_url": "http://127.0.0.1:8080",
        "cache.quota_bytes": str(DEFAULT_CACHE_QUOTA_BYTES),
        "privacy.mode": "local",
        "privacy.telemetry": "false",
        "tts.engine": "edge",
        "tts.voice": "vi-VN-HoaiMyNeural",
        "automation.chunked": "false",
        "automation.chunk_duration": "30",
        "automation.chunk_overlap": "2",
        "automation.chunk_concurrency": "4",
        "automation.chunk_retries": "2",
        "automation.stt_mode": "auto",
        "automation.stt_batch_size": "2",
        "automation.orchestrator_v2": "false",
    }


def validate_setting(key: str, value: str) -> str:
    if key not in SETTINGS_KEYS:
        raise ValueError(f"unknown settings key {key!r}")

    val = str(value).strip()

    if key in ("ai.device", "gpu.override"):
        if val not in ("auto", "cuda", "cpu"):
            raise ValueError(f"{key} must be one of auto/cuda/cpu")
    elif key == "privacy.mode":
        if val not in ("local", "cloud"):
            raise ValueError("privacy.mode must be one of local/cloud")
    elif key in ("privacy.telemetry", "automation.chunked", "automation.orchestrator_v2"):
        if val not in ("true", "false"):
            raise ValueError(f"{key} must be true or false")
    elif key == "tts.engine":
        if val not in ("edge", "piper"):
            raise ValueError("tts.engine must be one of edge/piper")
    elif key == "tts.voice":
        if not val:
            raise ValueError("tts.voice must not be empty")
    elif key == "automation.chunk_duration":
        if val not in ("20", "30", "45", "60"):
            raise ValueError("automation.chunk_duration must be one of 20/30/45/60")
    elif key == "automation.chunk_overlap":
        try:
            v = float(val)
        except ValueError:
            raise ValueError("automation.chunk_overlap must be a number")
        if not (0.0 <= v <= 10.0):
            raise ValueError("automation.chunk_overlap must be between 0 and 10")
    elif key == "automation.chunk_concurrency":
        try:
            v_int = int(val)
        except ValueError:
            raise ValueError("automation.chunk_concurrency must be an integer")
        if not (1 <= v_int <= 8):
            raise ValueError("automation.chunk_concurrency must be between 1 and 8")
    elif key == "automation.chunk_retries":
        try:
            v_int = int(val)
        except ValueError:
            raise ValueError("automation.chunk_retries must be an integer")
        if not (0 <= v_int <= 5):
            raise ValueError("automation.chunk_retries must be between 0 and 5")
    elif key == "automation.stt_mode":
        if val not in ("auto", "regular", "batched"):
            raise ValueError("automation.stt_mode must be one of auto/regular/batched")
    elif key == "automation.stt_batch_size":
        try:
            v_int = int(val)
        except ValueError:
            raise ValueError("automation.stt_batch_size must be an integer")
        if v_int not in (1, 2, 4):
            raise ValueError("automation.stt_batch_size must be one of 1/2/4")
    elif key == "cache.quota_bytes":
        try:
            bytes_val = int(val)
        except ValueError:
            raise ValueError("cache.quota_bytes must be a positive integer")
        if not (MIN_CACHE_QUOTA_BYTES <= bytes_val <= MAX_CACHE_QUOTA_BYTES):
            raise ValueError(f"cache.quota_bytes must be between {MIN_CACHE_QUOTA_BYTES} and {MAX_CACHE_QUOTA_BYTES} bytes")
    elif key in ("api.gemini.base_url", "api.local.base_url"):
        pass  # allow empty
    else:
        if not val:
            raise ValueError(f"{key} must not be empty")

    return val


def typed_value(key: str, value: str) -> Any:
    if key == "cache.quota_bytes":
        try:
            return int(value)
        except ValueError:
            return DEFAULT_CACHE_QUOTA_BYTES
    elif key == "privacy.telemetry":
        return value == "true"
    elif key == "automation.chunked":
        return value == "true"
    elif key == "automation.orchestrator_v2":
        return value == "true"
    return value


class SettingsRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_all(self) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        stored = {row["key"]: row["value"] for row in cursor.fetchall()}
        defaults = get_defaults()

        result = {}
        for key in SETTINGS_KEYS:
            val = stored.get(key, defaults.get(key, ""))
            result[key] = typed_value(key, val)
        return result

    def get(self, key: str) -> Any:
        if key not in SETTINGS_KEYS:
            raise ValueError(f"unknown settings key {key!r}")
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        defaults = get_defaults()
        raw_val = row["value"] if row else defaults.get(key, "")
        return typed_value(key, raw_val)

    def set(self, key: str, value: str, now: str) -> Dict[str, Any]:
        canonical = validate_setting(key, value)
        self.conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, canonical, now),
        )
        return self.get_all()
