"""Versioned schema migrations (v1..v9) matching Rust migrations.rs exactly."""

import sqlite3
from dataclasses import dataclass
from typing import List


@dataclass
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        name="create projects table",
        sql="""
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,           -- uuid v4
                name TEXT NOT NULL,
                source_video_path TEXT NOT NULL,
                status TEXT NOT NULL,          -- draft/analyzed/transcribed/translated/rendered
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                settings_json TEXT             -- project-level overrides
            );
        """,
    ),
    Migration(
        version=2,
        name="index projects.updated_at",
        sql="""
            CREATE INDEX idx_projects_updated_at ON projects(updated_at);
        """,
    ),
    Migration(
        version=3,
        name="create jobs table",
        sql="""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,           -- job_NNNN (canonical Job schema)
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                type TEXT NOT NULL,            -- transcribe/translate/subtitle/render
                status TEXT NOT NULL,          -- queued/running/succeeded/failed/cancelled
                progress REAL NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'queued',
                error_code TEXT,
                error_message TEXT,
                error_log TEXT,
                params_json TEXT NOT NULL DEFAULT '{}',
                retry_count INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE INDEX idx_jobs_project_id ON jobs(project_id);
            CREATE INDEX idx_jobs_status ON jobs(status);
        """,
    ),
    Migration(
        version=4,
        name="create cache entries table",
        sql="""
            CREATE TABLE cache_entries (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                key TEXT NOT NULL,               -- canonical content-addressed key (§3.7)
                stage TEXT NOT NULL,             -- audio/stt/tr/subtitle/render
                file_name TEXT NOT NULL,         -- <stage>_<sha256(key)>
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                PRIMARY KEY (project_id, key)
            );
            CREATE INDEX idx_cache_entries_last_access ON cache_entries(last_accessed_at);
            CREATE INDEX idx_cache_entries_stage ON cache_entries(stage);
        """,
    ),
    Migration(
        version=5,
        name="create glossary + character entries tables",
        sql="""
            CREATE TABLE glossary_entries (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                term TEXT NOT NULL,              -- canonical term (lowercased by service)
                translation TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, term)
            );
            CREATE INDEX idx_glossary_entries_project ON glossary_entries(project_id);

            CREATE TABLE character_entries (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, name)
            );
            CREATE INDEX idx_character_entries_project ON character_entries(project_id);
        """,
    ),
    Migration(
        version=6,
        name="create subtitle_cues table",
        sql="""
            CREATE TABLE subtitle_cues (
                id TEXT PRIMARY KEY,               -- uuid v4
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                cue_number INTEGER NOT NULL,       -- 1-based display order
                start REAL NOT NULL,               -- seconds
                end REAL NOT NULL,                 -- seconds
                text TEXT NOT NULL,                -- target subtitle text
                speaker TEXT,                      -- transcript speaker
                source_text TEXT,                  -- original transcript text
                status TEXT NOT NULL DEFAULT 'draft',  -- draft/translated/edited/approved
                style_json TEXT,                   -- per-cue style overrides
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, cue_number)
            );
            CREATE INDEX idx_subtitle_cues_project ON subtitle_cues(project_id);
        """,
    ),
    Migration(
        version=7,
        name="create app settings table",
        sql="""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,              -- whitelisted key
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """,
    ),
    Migration(
        version=8,
        name="create providers + provider_defaults tables",
        sql="""
            CREATE TABLE providers (
                id TEXT PRIMARY KEY,               -- slug for builtins (free/gemini/local/mock)
                name TEXT NOT NULL,
                provider_type TEXT NOT NULL,       -- capability area: 'translation'
                provider_kind TEXT NOT NULL,       -- worker registry kind: free/gemini/local/mock
                enabled INTEGER NOT NULL DEFAULT 1,
                base_url TEXT,                     -- provider-specific endpoint
                model TEXT,                        -- default model
                config_json TEXT NOT NULL DEFAULT '{}',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                last_test_status TEXT,
                last_test_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE provider_defaults (
                capability TEXT PRIMARY KEY,
                provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE
            );

            INSERT INTO providers
                (id, name, provider_type, provider_kind, enabled, base_url, model,
                 config_json, capabilities_json, last_test_status, last_test_at,
                 created_at, updated_at)
            VALUES
                ('free',   'FREE',           'translation', 'free',   1, 'http://127.0.0.1:8080', NULL,
                 '{}', '["translation","stt"]', NULL, NULL, '2026-08-01T00:00:00.000Z', '2026-08-01T00:00:00.000Z'),
                ('gemini', 'Gemini (cloud)', 'translation', 'gemini', 1, NULL, 'gemini-flash-lite-latest',
                 '{}', '["translation"]', NULL, NULL, '2026-08-01T00:00:00.000Z', '2026-08-01T00:00:00.000Z'),
                ('local',  'Local LLM',      'translation', 'local',  1, 'http://127.0.0.1:8080', NULL,
                 '{}', '["translation"]', NULL, NULL, '2026-08-01T00:00:00.000Z', '2026-08-01T00:00:00.000Z'),
                ('mock',   'Mock (offline)', 'translation', 'mock',   1, NULL, NULL,
                 '{}', '["translation"]', NULL, NULL, '2026-08-01T00:00:00.000Z', '2026-08-01T00:00:00.000Z');

            INSERT INTO provider_defaults (capability, provider_id) VALUES
                ('translation', 'free'),
                ('stt', 'free'),
                ('tts', 'free');
        """,
    ),
    Migration(
        version=9,
        name="create tasks table for orchestration",
        sql="""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,                -- uuid v4
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL,             -- transcribe/translate/subtitle/tts/render/logo/chunk
                stage TEXT NOT NULL,                 -- display stage name
                status TEXT NOT NULL DEFAULT 'queued',  -- queued/ready/running/succeeded/failed/cancelled/blocked
                progress REAL NOT NULL DEFAULT 0.0,  -- 0.0 to 1.0
                depends_on TEXT NOT NULL DEFAULT '[]',  -- JSON array of task IDs
                params_json TEXT,                    -- task-specific parameters
                input_fingerprint TEXT,              -- sha256 fingerprint
                result_json TEXT,                    -- task output metadata
                error_code TEXT,                     -- structured error code
                error_message TEXT,                  -- human-readable error
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE INDEX idx_tasks_job_id ON tasks(job_id);
            CREATE INDEX idx_tasks_status ON tasks(status);
            CREATE INDEX idx_tasks_job_status ON tasks(job_id, status);
        """,
    ),
]


def current_version(conn: sqlite3.Connection) -> int:
    """Return the current PRAGMA user_version."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA user_version;")
    row = cursor.fetchone()
    return row[0] if row else 0


def apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Apply a single migration inside an IMMEDIATE transaction."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.executescript(migration.sql)
        conn.execute(f"PRAGMA user_version = {migration.version};")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration v{migration.version} ({migration.name}) failed: {e}") from e


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run all pending migrations in version order."""
    cur_ver = current_version(conn)
    for migration in MIGRATIONS:
        if migration.version > cur_ver:
            apply_migration(conn, migration)
