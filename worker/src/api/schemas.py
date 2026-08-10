# DO NOT EDIT - generated from schemas/*.schema.json by scripts/generate_schemas.py
# Source of truth: schemas/ (single source of truth - MASTER_PLAN.md 24 / TASK-007).
# Re-run `python scripts/generate_schemas.py` after changing any schema file.


from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    RootModel,
    confloat,
    conint,
    constr,
)



class HealthStatus(RootModel[Literal['ok']]):
    root: Literal['ok']


class HealthResponse(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    status: HealthStatus
    version: constr(min_length=1)
    gpu: None


class WorkerState(
    RootModel[Literal['stopped', 'starting', 'ready', 'stopping', 'failed']]
):
    root: Literal['stopped', 'starting', 'ready', 'stopping', 'failed']


class WorkerStateInfo(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    state: WorkerState
    pid: conint(ge=0) | None
    port: conint(ge=0, le=65535) | None
    restarts: conint(ge=0)
    last_error: str | None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: constr(pattern=r'^E_[A-Z0-9_]+$')
    message: constr(min_length=1)
    recoverable: bool


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    error: ErrorEnvelope


class JobType(RootModel[Literal['transcribe', 'translate', 'subtitle', 'render']]):
    root: Literal['transcribe', 'translate', 'subtitle', 'render']


class JobStatus(
    RootModel[Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']]
):
    root: Literal['queued', 'running', 'succeeded', 'failed', 'cancelled']


class ISO8601(RootModel[str]):
    root: str


class ISO8601OrNull(RootModel[str | None]):
    root: str | None


class Job(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: constr(pattern=r'^job_[0-9]+$')
    project_id: constr(min_length=1)
    type: JobType
    status: JobStatus
    progress: confloat(ge=0.0, le=1.0)
    stage: constr(min_length=1)
    error_code: str | None
    error_message: str | None
    params: dict[str, Any]
    created_at: ISO8601
    started_at: ISO8601OrNull | None
    finished_at: ISO8601OrNull | None


class Rational(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    numerator: int
    denominator: PositiveInt


class Rotation(RootModel[Literal[0, 90, 180, 270]]):
    root: Literal[0, 90, 180, 270]


class VideoStream(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    index: conint(ge=0)
    codec: str | None
    profile: str | None
    width: conint(ge=0) | None
    height: conint(ge=0) | None
    fps: Rational
    pixel_format: str | None
    aspect_ratio: str | None
    bitrate: conint(ge=0) | None
    duration: confloat(ge=0.0) | None


class AudioStream(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    index: conint(ge=0)
    codec: str | None
    channels: conint(ge=0) | None
    sample_rate: conint(ge=0) | None
    bitrate: conint(ge=0) | None
    duration: confloat(ge=0.0) | None
    language: str | None


class SubtitleStream(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    index: conint(ge=0)
    codec: str | None
    language: str | None
    title: str | None
    duration: confloat(ge=0.0) | None


class MediaMetadata(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Literal[1]
    duration: confloat(ge=0.0)
    width: conint(ge=0) | None
    height: conint(ge=0) | None
    fps: Rational
    codec: str | None
    bitrate: conint(ge=0) | None
    rotation: Rotation
    format: constr(min_length=1)
    aspect_ratio: str | None
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]
    subtitle_streams: list[SubtitleStream]


class Model(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: constr(pattern=r'^[a-z0-9][a-z0-9._-]*$', min_length=1)
    name: constr(min_length=1)
    version: constr(min_length=1)
    source: constr(min_length=1)
    download_url: constr(min_length=1)
    expected_size_bytes: conint(ge=0)
    checksum: constr(pattern=r'^([0-9a-f]{64}|)$')
    license: constr(min_length=1)
    required_vram_mb: confloat(ge=0.0)
    supported_backend: list[Literal['faster-whisper', 'whisper-cpp']] = Field(
        ..., min_length=1
    )


class ProjectStatus(
    RootModel[Literal['draft', 'analyzed', 'transcribed', 'translated', 'rendered']]
):
    root: Literal['draft', 'analyzed', 'transcribed', 'translated', 'rendered']


class Project(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: constr(
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    )
    name: constr(min_length=1, max_length=200)
    source_video_path: constr(min_length=1)
    status: ProjectStatus
    created_at: constr(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
    updated_at: constr(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
    settings_json: str | None


class SubtitleStyle(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    font: constr(min_length=1)
    font_size: conint(ge=1, le=500)
    stroke: conint(ge=0)
    shadow: conint(ge=0)
    position: Literal['bottom_center', 'top_center']
    bg_box: bool
    max_chars_per_line: conint(ge=1)
    max_cps: conint(ge=1)


class Cue(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    cue_number: conint(ge=1)
    start: confloat(ge=0.0)
    end: confloat(ge=0.0)
    text: constr(min_length=1)


class SubtitleOutput(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    ass_path: str | None = None
    srt_path: str | None = None


class Subtitle(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Literal[1]
    project_id: constr(min_length=1)
    style: SubtitleStyle
    cues: list[Cue] = Field(..., min_length=1)
    output: SubtitleOutput


class Word(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    word: constr(min_length=1)
    start: confloat(ge=0.0)
    end: confloat(ge=0.0)
    speaker: str | None = None


class TranslationItem(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    idx: conint(ge=0)
    segment_id: constr(pattern=r'^seg_[0-9]+$')
    source_text: constr(min_length=1)
    translated_text: constr(min_length=1)
    confidence: confloat(ge=0.0, le=1.0)


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    id: constr(pattern=r'^seg_[0-9]+$')
    idx: conint(ge=0)
    speaker: str | None = None
    start: confloat(ge=0.0)
    end: confloat(ge=0.0)
    text: constr(min_length=1)
    language: constr(min_length=2, max_length=8)
    confidence: confloat(ge=0.0, le=1.0)
    words: list[Word] | None = None


class Transcript(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Literal[1]
    project_id: constr(min_length=1)
    language: constr(min_length=2, max_length=8)
    model: constr(min_length=1)
    segments: list[TranscriptSegment] = Field(..., min_length=1)


class TranslationBlock(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    block_idx: conint(ge=0)
    translations: list[TranslationItem] = Field(..., min_length=1)


class Translation(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Literal[1]
    target_language: constr(min_length=2, max_length=8)
    model: constr(min_length=1)
    blocks: list[TranslationBlock] = Field(..., min_length=1)
