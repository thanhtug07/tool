"""Subtitle cues repository for CRUD operations on the subtitle_cues table."""

import sqlite3
from typing import Optional, List
from src.db.models import SubtitleCue


def _row_to_cue(row: sqlite3.Row) -> SubtitleCue:
    return SubtitleCue(
        id=row["id"],
        project_id=row["project_id"],
        cue_number=int(row["cue_number"]),
        start=float(row["start"]),
        end=float(row["end"]),
        text=row["text"],
        speaker=row["speaker"],
        source_text=row["source_text"],
        status=row["status"],
        style_json=row["style_json"],
        updated_at=row["updated_at"],
    )


class SubtitleRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self, project_id: str) -> List[SubtitleCue]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, cue_number, start, end, text, speaker, source_text, status, style_json, updated_at
            FROM subtitle_cues WHERE project_id = ? ORDER BY cue_number
            """,
            (project_id,),
        )
        return [_row_to_cue(row) for row in cursor.fetchall()]

    def insert_many(self, cues: List[SubtitleCue]) -> None:
        for cue in cues:
            self.conn.execute(
                """
                INSERT INTO subtitle_cues (id, project_id, cue_number, start, end, text, speaker, source_text, status, style_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cue.id,
                    cue.project_id,
                    cue.cue_number,
                    cue.start,
                    cue.end,
                    cue.text,
                    cue.speaker,
                    cue.source_text,
                    cue.status,
                    cue.style_json,
                    cue.updated_at,
                ),
            )

    def delete_project(self, project_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM subtitle_cues WHERE project_id = ?", (project_id,))
        return cursor.rowcount > 0

    def get(self, cue_id: str) -> Optional[SubtitleCue]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, project_id, cue_number, start, end, text, speaker, source_text, status, style_json, updated_at
            FROM subtitle_cues WHERE id = ?
            """,
            (cue_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_cue(row)

    def update(
        self,
        cue_id: str,
        start: float,
        end: float,
        text: str,
        speaker: Optional[str],
        status: str,
        updated_at: str,
    ) -> Optional[SubtitleCue]:
        cursor = self.conn.execute(
            """
            UPDATE subtitle_cues
            SET start = ?, end = ?, text = ?, speaker = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (start, end, text, speaker, status, updated_at, cue_id),
        )
        if cursor.rowcount == 0:
            return None
        return self.get(cue_id)
