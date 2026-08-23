"""Project HTTP routes (web-mode parity for Tauri project commands).

Adds endpoints NOT already covered by web_routes.py:
- POST /api/projects               → create a project
- GET  /api/projects/by-source     → find by source video path
- GET  /api/projects/{id}          → get project by ID

Note: GET /api/projects (list) is already in web_routes.py.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.core.db import (
    create_project,
    delete_project,
    find_project_by_source_video,
    get_project_by_id,
    save_project,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectOut(BaseModel):
    """Wire representation matching the Rust Project struct."""
    id: str
    name: str
    source_video_path: str
    status: str
    created_at: str
    updated_at: str
    settings_json: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    videoPath: str = Field(min_length=1)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_new_project(request: ProjectCreateRequest) -> ProjectOut:
    """Create a project from a source video path."""
    project = create_project(request.name, request.videoPath)
    return ProjectOut(**project)


@router.get("/by-source", response_model=ProjectOut | None)
def find_by_source(video_path: str = Query(min_length=1)) -> ProjectOut | None:
    """Find an existing project by source video path (case-insensitive)."""
    project = find_project_by_source_video(video_path)
    if project is None:
        return None
    return ProjectOut(**project)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str) -> ProjectOut:
    """Load one project by ID."""
    project = get_project_by_id(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id!r} not found",
        )
    return ProjectOut(**project)



@router.post("/{project_id}/save", response_model=ProjectOut)
def save_existing_project(project_id: str) -> ProjectOut:
    """Touch updated_at on a project (auto-save signal)."""
    try:
        project = save_project(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id!r} not found",
        )
    return ProjectOut(**project)


@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
def delete_existing_project(project_id: str) -> dict:
    """Delete a project by ID."""
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id!r} not found",
        )
    return {"deleted": True, "id": project_id}
