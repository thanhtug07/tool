/**
 * Canonical shared contracts — TS view of `schemas/*.schema.json`.
 *
 * `schemas/` is the single source of truth (MASTER_PLAN.md §24 / TASK-007).
 * This file is hand-maintained to mirror the JSON Schemas (the generator only
 * covers Python today). `src/types/api.test.ts` checks every canonical example
 * fixture against these types and against the schemas to catch drift.
 *
 * Do not introduce fields here that are not in the corresponding schema file.
 */

// ---- schemas/api.schema.json ------------------------------------------------

export type HealthStatus = "ok";

export type HealthResponse = {
  status: HealthStatus;
  version: string;
  gpu: null;
};

export type WorkerState = "stopped" | "starting" | "ready" | "stopping" | "failed";

export type WorkerStateInfo = {
  state: WorkerState;
  pid: number | null;
  port: number | null;
  restarts: number;
  last_error: string | null;
};

export type ErrorEnvelope = {
  code: string;
  message: string;
  recoverable: boolean;
};

export type ErrorResponse = {
  error: ErrorEnvelope;
};

// ---- schemas/job.schema.json ------------------------------------------------

export type JobType = "transcribe" | "translate" | "subtitle" | "render";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type Job = {
  id: string;
  project_id: string;
  type: JobType;
  status: JobStatus;
  progress: number;
  stage: string;
  error_code: string | null;
  error_message: string | null;
  params: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

// ---- schemas/subtitle.schema.json -------------------------------------------

export type SubtitlePosition = "bottom_center" | "top_center";

export type SubtitleStyle = {
  font: string;
  font_size: number;
  stroke: number;
  shadow: number;
  position: SubtitlePosition;
  bg_box: boolean;
  max_chars_per_line: number;
  max_cps: number;
};

export type SubtitleCue = {
  cue_number: number;
  start: number;
  end: number;
  text: string;
};

export type SubtitleOutput = {
  ass_path: string | null;
  srt_path: string | null;
};

export type Subtitle = {
  schema_version: 1;
  project_id: string;
  style: SubtitleStyle;
  cues: SubtitleCue[];
  output: SubtitleOutput;
};

// ---- schemas/transcript.schema.json -----------------------------------------

export type TranscriptWord = {
  word: string;
  start: number;
  end: number;
  speaker?: string;
};

export type TranscriptSegment = {
  id: string;
  idx: number;
  speaker?: string;
  start: number;
  end: number;
  text: string;
  language: string;
  confidence: number;
  words?: TranscriptWord[];
};

export type Transcript = {
  schema_version: 1;
  project_id: string;
  language: string;
  model: string;
  segments: TranscriptSegment[];
};

// ---- schemas/project.schema.json --------------------------------------------

export type ProjectStatus = "draft" | "analyzed" | "transcribed" | "translated" | "rendered";

export type Project = {
  id: string;
  name: string;
  source_video_path: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
  settings_json: string | null;
};

// ---- schemas/translation.schema.json ----------------------------------------

export type TranslationItem = {
  idx: number;
  segment_id: string;
  source_text: string;
  translated_text: string;
  confidence: number;
};

export type TranslationBlock = {
  block_idx: number;
  translations: TranslationItem[];
};

export type Translation = {
  schema_version: 1;
  target_language: string;
  model: string;
  blocks: TranslationBlock[];
};
