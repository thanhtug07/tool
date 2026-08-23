"""Configuration and path resolution for AI Video Localization Studio backend."""

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the data directory root.

    Precedence:
    1. AIVS_DATA_DIR environment variable
    2. %LOCALAPPDATA%/ai-video-localization (Windows)
    3. ~/.local/share/ai-video-localization (Linux/macOS)
    """
    env_dir = os.environ.get("AIVS_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ai-video-localization"

    return Path.home() / ".local" / "share" / "ai-video-localization"


def get_db_path() -> Path:
    """Return the absolute path to SQLite database app.db."""
    return get_data_dir() / "app.db"


def get_projects_dir() -> Path:
    """Return the absolute path to the projects directory."""
    return get_data_dir() / "projects"


def ensure_data_dirs() -> None:
    """Ensure the root data directory and projects directory exist."""
    get_data_dir().mkdir(parents=True, exist_ok=True)
    get_projects_dir().mkdir(parents=True, exist_ok=True)


def get_project_dir(project_id: str) -> Path:
    """Return project working directory and ensure video/, cache/, output/ exist."""
    proj_dir = get_projects_dir() / project_id
    (proj_dir / "video").mkdir(parents=True, exist_ok=True)
    (proj_dir / "cache").mkdir(parents=True, exist_ok=True)
    (proj_dir / "output").mkdir(parents=True, exist_ok=True)
    return proj_dir
