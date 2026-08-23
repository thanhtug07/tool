"""Database connection and connection management for SQLite."""

import sqlite3
from pathlib import Path
from typing import Optional, Generator
from contextlib import contextmanager

from src.core.config import get_db_path, ensure_data_dirs


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Create a sqlite3 connection configured with PRAGMAs."""
    if db_path is None:
        ensure_data_dirs()
        db_path = get_db_path()

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


class Database:
    """Database handle wrapping connection or path."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_db_path()
        self._conn: Optional[sqlite3.Connection] = None

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self.conn()
        connection.execute("BEGIN IMMEDIATE;")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
