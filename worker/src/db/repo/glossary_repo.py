"""Glossary repository for CRUD operations on the glossary_entries table."""

import sqlite3
from typing import Optional, List
from src.db.models import GlossaryEntry


def fnv1a_step(hash_val: int, byte_val: int) -> int:
    return ((hash_val ^ byte_val) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF


class GlossaryRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self, project_id: str) -> List[GlossaryEntry]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT project_id, term, translation, updated_at
            FROM glossary_entries WHERE project_id = ? ORDER BY term
            """,
            (project_id,),
        )
        return [
            GlossaryEntry(
                project_id=row["project_id"],
                term=row["term"],
                translation=row["translation"],
                updated_at=row["updated_at"],
            )
            for row in cursor.fetchall()
        ]

    def upsert(self, entry: GlossaryEntry) -> None:
        term = entry.term.lower().strip()
        self.conn.execute(
            """
            INSERT INTO glossary_entries (project_id, term, translation, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, term) DO UPDATE SET
                translation = excluded.translation,
                updated_at = excluded.updated_at
            """,
            (entry.project_id, term, entry.translation, entry.updated_at),
        )

    def delete(self, project_id: str, term: str) -> bool:
        term_clean = term.lower().strip()
        cursor = self.conn.execute(
            "DELETE FROM glossary_entries WHERE project_id = ? AND term = ?",
            (project_id, term_clean),
        )
        return cursor.rowcount > 0

    def fingerprint(self, project_id: str) -> str:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT term, translation FROM glossary_entries WHERE project_id = ? ORDER BY term",
            (project_id,),
        )
        rows = cursor.fetchall()
        hash_val = 0xCBF29CE484222325
        for row in rows:
            term = row["term"]
            translation = row["translation"]
            for b in term.encode("utf-8"):
                hash_val = fnv1a_step(hash_val, b)
            hash_val = fnv1a_step(hash_val, 0)
            for b in translation.encode("utf-8"):
                hash_val = fnv1a_step(hash_val, b)
            hash_val = fnv1a_step(hash_val, 0xFF)
        return f"{hash_val:016x}"
