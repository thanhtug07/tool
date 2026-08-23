"""Character entries repository for CRUD operations on character_entries table."""

import sqlite3
from typing import List
from src.db.models import CharacterEntry


class CharacterRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self, project_id: str) -> List[CharacterEntry]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT project_id, name, description, updated_at
            FROM character_entries WHERE project_id = ? ORDER BY name
            """,
            (project_id,),
        )
        return [
            CharacterEntry(
                project_id=row["project_id"],
                name=row["name"],
                description=row["description"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def upsert(self, entry: CharacterEntry) -> None:
        self.conn.execute(
            """
            INSERT INTO character_entries (project_id, name, description, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, name) DO UPDATE SET
                description = excluded.description,
                updated_at = excluded.updated_at
            """,
            (entry.project_id, entry.name, entry.description, entry.updated_at),
        )

    def delete(self, project_id: str, name: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM character_entries WHERE project_id = ? AND name = ?",
            (project_id, name),
        )
        return cursor.rowcount > 0
