"""Read/write access to the shared ``app.db`` SQLite database.

Now that Tauri/Rust has been removed, the worker is the sole DB owner and
can open a normal read-write connection.  WAL mode is still used for safety.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# ------------------------------------------------------------------
# Path resolution
# ------------------------------------------------------------------

_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "ai-video-localization"


def resolve_db_path() -> Path:
    env = os.environ.get("APP_DB_PATH", "").strip()
    if env:
        return Path(env)
    win_data = os.environ.get("LOCALAPPDATA")
    if win_data:
        return Path(win_data) / "ai-video-localization" / "app.db"
    return _DEFAULT_DB_DIR / "app.db"


# ------------------------------------------------------------------
# Connection helpers
# ------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    global _conn
    with _db_lock:
        if _conn is not None:
            return _conn
        db_path = resolve_db_path()
        if not db_path.is_file():
            logger.warning("app.db not found at %s — queries will be empty", db_path)
            _conn = sqlite3.connect(":memory:", check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            return _conn
        logger.info("opening project DB: %s", db_path)
        _conn = sqlite3.connect(str(db_path), timeout=5, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.execute("PRAGMA foreign_keys=ON")
        return _conn


def close_db() -> None:
    global _conn
    with _db_lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ------------------------------------------------------------------
# Settings queries (mirrors Rust SettingsService)
# ------------------------------------------------------------------

_SETTINGS_KEYS = (
    "ai.model", "ai.device", "ai.preset", "gpu.override",
    "api.gemini.base_url", "api.gemini.model", "api.local.base_url",
    "cache.quota_bytes", "privacy.mode", "privacy.telemetry",
    "tts.engine", "tts.voice",
    "automation.chunked", "automation.chunk_duration", "automation.chunk_overlap",
    "automation.chunk_concurrency", "automation.chunk_retries",
    "automation.stt_mode", "automation.stt_batch_size",
)

_SETTINGS_DEFAULTS: dict[str, Any] = {
    "ai.model": "large-v3",
    "ai.device": "auto",
    "ai.preset": "balanced",
    "gpu.override": "auto",
    "api.gemini.base_url": "",
    "api.gemini.model": "gemini-flash-lite-latest",
    "api.local.base_url": "http://127.0.0.1:8080",
    "cache.quota_bytes": 10 * 1024 * 1024 * 1024,
    "privacy.mode": "local",
    "privacy.telemetry": False,
    "tts.engine": "edge",
    "tts.voice": "vi-VN-HoaiMyNeural",
    "automation.chunked": "false",
    "automation.chunk_duration": "30",
    "automation.chunk_overlap": "2",
    "automation.chunk_concurrency": "4",
    "automation.chunk_retries": "2",
    "automation.stt_mode": "auto",
    "automation.stt_batch_size": "2",
}


def _typed_value(key: str, value: str) -> Any:
    if key == "cache.quota_bytes":
        try:
            return int(value)
        except ValueError:
            return 10 * 1024 * 1024 * 1024
    if key == "privacy.telemetry":
        return value == "true"
    return value


def get_all_settings() -> dict[str, Any]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        stored = {row["key"]: row["value"] for row in rows}
    except Exception:
        logger.exception("get_all_settings failed")
        stored = {}
    result: dict[str, Any] = {}
    for key in _SETTINGS_KEYS:
        raw = stored.get(key)
        if raw is not None:
            result[key] = _typed_value(key, raw)
        else:
            result[key] = _SETTINGS_DEFAULTS.get(key)
    return result


def set_setting(key: str, value: str) -> dict[str, Any]:
    if key not in _SETTINGS_KEYS:
        raise ValueError(f"unknown settings key {key!r}")
    conn = _get_conn()
    now = _utcnow()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value.strip(), now),
    )
    conn.commit()
    logger.info("settings updated: %s", key)
    return get_all_settings()


# ------------------------------------------------------------------
# Project queries (mirrors Rust ProjectRepo)
# ------------------------------------------------------------------

_PROJECT_COLUMNS = "id, name, source_video_path, status, created_at, updated_at, settings_json"


def _normalize_source_path(path: str) -> str:
    return path.strip().lower().replace("\\", "/")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def find_project_by_source_video(video_path: str) -> dict[str, Any] | None:
    conn = _get_conn()
    needle = _normalize_source_path(video_path)
    try:
        for row in conn.execute(f"SELECT {_PROJECT_COLUMNS} FROM projects"):
            project = _row_to_dict(row)
            if _normalize_source_path(project["source_video_path"]) == needle:
                return project
    except Exception:
        logger.exception("find_project_by_source_video failed")
    return None


def list_projects() -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            f"SELECT {_PROJECT_COLUMNS} FROM projects ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        logger.exception("list_projects failed")
        return []


def get_project_by_id(project_id: str) -> dict[str, Any] | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        logger.exception("get_project_by_id failed")
        return None


def create_project(name: str, source_video_path: str) -> dict[str, Any]:
    import uuid
    conn = _get_conn()
    now = _utcnow()
    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'draft', ?, ?)",
        (project_id, name, source_video_path, now, now),
    )
    conn.commit()
    logger.info("project created: %s", project_id)
    return get_project_by_id(project_id)  # type: ignore[return-value]


def save_project(project_id: str) -> dict[str, Any]:
    """Touch updated_at on a project (auto-save signal)."""
    conn = _get_conn()
    now = _utcnow()
    conn.execute(
        "UPDATE projects SET updated_at = ? WHERE id = ?",
        (now, project_id),
    )
    conn.commit()
    logger.info("project saved: %s", project_id)
    project = get_project_by_id(project_id)
    if project is None:
        raise ValueError(f"Project {project_id!r} not found")
    return project


def delete_project(project_id: str) -> bool:
    """Delete a project by ID. Returns True if deleted."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info("project deleted: %s", project_id)
    return deleted
