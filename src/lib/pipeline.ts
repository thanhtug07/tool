/** Real job types the backend pipeline supports (schemas/job.schema.json). */
export const JOB_TYPES = ["transcribe", "translate", "subtitle", "render"] as const;
export type JobType = (typeof JOB_TYPES)[number];

export const JOB_TYPE_LABELS: Record<JobType, string> = {
  transcribe: "Transcribe",
  translate: "Translate",
  subtitle: "Subtitles",
  render: "Render",
};

export function jobTypeLabel(type: string): string {
  return JOB_TYPE_LABELS[type as JobType] ?? type;
}

/** Human-readable label for a job's `stage` string reported by the runner. */
const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  "extract-audio": "Extracting audio",
  transcribe: "Transcribing",
  translate: "Translating",
  subtitle: "Generating subtitles",
  render: "Rendering",
  done: "Finalizing",
};

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? (stage ? stage : "Working…");
}

/** Icons/tones for job status chips. */
export const STATUS_TONES: Record<string, string> = {
  succeeded: "text-emerald-400",
  failed: "text-red-400",
  cancelled: "text-muted-foreground",
  running: "text-sky-400",
  queued: "text-amber-400",
};

/** Wall-clock processing time of a job in ms (0 when not finished). */
export function jobProcessingMs(job: {
  started_at: string | null;
  finished_at: string | null;
}): number {
  if (!job.started_at || !job.finished_at) return 0;
  const start = Date.parse(job.started_at);
  const end = Date.parse(job.finished_at);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return 0;
  return end - start;
}

/** Whether an ISO timestamp falls on the user's local "today". */
export function isToday(iso: string): boolean {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}
