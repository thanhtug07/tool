"""Project repository for CRUD operations on the projects table."""

import sqlite3
from typing import Optional, List
from src.db.models import Project, ProjectStatus


def normalize_source_path(path: str) -> str:
    return path.strip().lower().replace("\\", "/")


class ProjectRepo:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, project: Project) -> None:
        self.conn.execute(
            """
            INSERT INTO projects (id, name, source_video_path, status, created_at, updated_at, settings_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.source_video_path,
                project.status.value if isinstance(project.status, ProjectStatus) else project.status,
                project.created_at,
                project.updated_at,
                project.settings_json,
            ),
        )

    def get(self, project_id: str) -> Optional[Project]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, name, source_video_path, status, created_at, updated_at, settings_json FROM projects WHERE id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Project(
            id=row["id"],
            name=row["name"],
            source_video_path=row["source_video_path"],
            status=ProjectStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            settings_json=row["settings_json"],
        )

    def find_by_source_path(self, path: str) -> Optional[Project]:
        needle = normalize_source_path(path)
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, source_video_path, status, created_at, updated_at, settings_json FROM projects")
        for row in cursor.fetchall():
            if normalize_source_path(row["source_video_path"]) == needle:
                return Project(
                    id=row["id"],
                    name=row["name"],
                    source_video_path=row["source_video_path"],
                    status=ProjectStatus(row["status"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    settings_json=row["settings_json"],
                )
        return None

    def list(self) -> List[Project]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, name, source_video_path, status, created_at, updated_at, settings_json FROM projects ORDER BY updated_at DESC"
        )
        out = []
        for row in cursor.fetchall():
            out.append(
                Project(
                    id=row["id"],
                    name=row["name"],
                    source_video_path=row["source_video_path"],
                    status=ProjectStatus(row["status"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    settings_json=row["settings_json"],
                )
            )
        return out

    def update(self, project: Project) -> bool:
        cursor = self.conn.execute(
            """
            UPDATE projects
            SET name = ?, source_video_path = ?, status = ?, updated_at = ?, settings_json = ?
            WHERE id = ?
            """,
            (
                project.name,
                project.source_video_path,
                project.status.value if isinstance(project.status, ProjectStatus) else project.status,
                project.updated_at,
                project.settings_json,
                project.id,
            ),
        )
        return cursor.rowcount > 0

    def touch(self, project_id: str, now: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (now, project_id),
        )
        return cursor.rowcount > 0

    def delete(self, project_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cursor.rowcount > 0
