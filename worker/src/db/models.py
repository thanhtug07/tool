"""Database models and enums matching canonical schemas and Rust domain types."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Dict, List
import json


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    ANALYZED = "analyzed"
    TRANSCRIBED = "transcribed"
    TRANSLATED = "translated"
    RENDERED = "rendered"


class JobType(str, Enum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"
    SUBTITLE = "subtitle"
    TTS = "tts"
    RENDER = "render"
    LOGO = "logo"
    AUDIO = "audio"
    CHUNK = "chunk"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def can_transition(self, to: "JobStatus") -> bool:
        allowed = {
            (JobStatus.QUEUED, JobStatus.RUNNING),
            (JobStatus.QUEUED, JobStatus.CANCELLED),
            (JobStatus.RUNNING, JobStatus.QUEUED),  # crash resume
            (JobStatus.RUNNING, JobStatus.SUCCEEDED),
            (JobStatus.RUNNING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.CANCELLED),
            (JobStatus.FAILED, JobStatus.QUEUED),  # manual retry
            (JobStatus.CANCELLED, JobStatus.QUEUED),
        }
        return (self, to) in allowed

    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class TaskType(str, Enum):
    TRANSCRIBE = "transcribe"
    TRANSLATE = "translate"
    SUBTITLE = "subtitle"
    TTS = "tts"
    RENDER = "render"
    LOGO = "logo"
    CHUNK = "chunk"
    AUDIO = "audio"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    def can_transition(self, to: "TaskStatus") -> bool:
        allowed = {
            (TaskStatus.QUEUED, TaskStatus.READY),
            (TaskStatus.QUEUED, TaskStatus.BLOCKED),
            (TaskStatus.QUEUED, TaskStatus.CANCELLED),
            (TaskStatus.READY, TaskStatus.RUNNING),
            (TaskStatus.READY, TaskStatus.CANCELLED),
            (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
            (TaskStatus.RUNNING, TaskStatus.QUEUED),  # retry
            (TaskStatus.RUNNING, TaskStatus.FAILED),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED),
            (TaskStatus.BLOCKED, TaskStatus.QUEUED),
            (TaskStatus.BLOCKED, TaskStatus.CANCELLED),
        }
        return (self, to) in allowed

    def is_terminal(self) -> bool:
        return self in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class Project:
    id: str
    name: str
    source_video_path: str
    status: ProjectStatus
    created_at: str
    updated_at: str
    settings_json: Optional[str] = None


@dataclass
class Job:
    id: str
    project_id: str
    job_type: JobType
    status: JobStatus
    progress: float
    stage: str
    created_at: str
    updated_at: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_log: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    cancel_requested: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass
class Task:
    id: str
    job_id: str
    task_type: TaskType
    stage: str
    status: TaskStatus
    progress: float
    depends_on: str  # JSON array string
    created_at: str
    updated_at: str
    params_json: Optional[str] = None
    input_fingerprint: Optional[str] = None
    result_json: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_attempts: int = 3
    cancel_requested: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass
class SubtitleCue:
    id: str
    project_id: str
    cue_number: int
    start: float
    end: float
    text: str
    updated_at: str
    speaker: Optional[str] = None
    source_text: Optional[str] = None
    status: str = "draft"
    style_json: Optional[str] = None


@dataclass
class GlossaryEntry:
    project_id: str
    term: str
    translation: str
    updated_at: str


@dataclass
class CharacterEntry:
    project_id: str
    name: str
    description: str
    updated_at: str


@dataclass
class Setting:
    key: str
    value: str
    updated_at: str


@dataclass
class Provider:
    id: str
    name: str
    provider_type: str
    provider_kind: str
    enabled: bool
    created_at: str
    updated_at: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    config_json: str = "{}"
    capabilities_json: str = "[]"
    last_test_status: Optional[str] = None
    last_test_at: Optional[str] = None
