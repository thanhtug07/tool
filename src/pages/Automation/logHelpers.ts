import type { Job, JobLogEvent } from "@/api/job";
import type { DerivedStageRun, StageKey } from "./automation";

/**
 * Pure helpers for the Automation Live Log. Everything here is derived from
 * *real* backend state (job rows, `job:status` / `job:log` events) — nothing
 * is fabricated to make the UI look busy.
 */

export type LogLevel = "info" | "success" | "warn" | "error";

export type LogEntry = {
  /** Monotonic id (dedupe / key). */
  id: number;
  /** Epoch ms when the line arrived. */
  time: number;
  level: LogLevel;
  message: string;
};

export const DEFAULT_MAX_LOG_ENTRIES = 500;
export const MAX_LOG_ENTRIES_OPTIONS = [200, 500, 1000] as const;

/**
 * Append one console line, keeping only the newest `max` entries (cap is the
 * performance guard for long videos — the DOM never renders thousands of rows).
 */
export function appendLogEntry(entries: LogEntry[], entry: LogEntry, max: number): LogEntry[] {
  if (max <= 0) return [];
  const next = entries.length >= max ? entries.slice(entries.length - max + 1) : entries;
  next.push(entry);
  return next;
}

/**
 * Estimate remaining time from *real* progress velocity.
 * Returns `null` (so the UI hides ETA) whenever there is not enough data:
 * before meaningful progress, after completion, or on a stalled pipeline.
 */
export function computeEta(fraction: number, elapsedMs: number): number | null {
  if (!Number.isFinite(fraction) || !Number.isFinite(elapsedMs)) return null;
  if (fraction <= 0.03 || fraction >= 0.999) return null;
  if (elapsedMs <= 0) return null;
  return Math.round(elapsedMs * ((1 - fraction) / fraction));
}

export function formatEta(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Human-readable stage label (mirrors the pipeline checklist). */
export function stageLabel(key: StageKey): string {
  const labels: Record<StageKey, string> = {
    transcribe: "Transcription",
    translate: "Translation",
    subtitle: "Subtitle generation",
    tts: "Voice generation",
    render: "Final rendering",
    audio: "Audio processing",
    logo: "Logo removal",
    chunk: "Chunked pipeline",
  };
  return labels[key] ?? key;
}

/**
 * Backfill console lines from real persisted job rows (survives an app reload:
 * the raw per-segment lines are ephemeral, but stage-level history is restored
 * from the database). Returns entries already ordered oldest → newest.
 */
export function backfillFromJobs(
  planStages: { key: StageKey; jobId: string | null }[],
  jobs: Job[],
  now: number,
): LogEntry[] {
  const byId = new Map(jobs.map((j) => [j.id, j]));
  const entries: LogEntry[] = [];
  let id = 0;
  for (const stage of planStages) {
    if (!stage.jobId) continue;
    const job = byId.get(stage.jobId);
    if (!job) continue;
    const finishedAt = job.finished_at ? Date.parse(job.finished_at) : NaN;
    const startedAt = job.started_at ? Date.parse(job.started_at) : NaN;
    if (!Number.isNaN(startedAt)) {
      entries.push({
        id: id++,
        time: startedAt,
        level: "info",
        message: `${stageLabel(stage.key)} started`,
      });
    }
    if (!Number.isNaN(finishedAt)) {
      const level: LogLevel =
        job.status === "failed" ? "error" : job.status === "cancelled" ? "warn" : "success";
      const suffix =
        job.status === "failed"
          ? ` — ${job.error_message ?? job.error_code ?? "failed"}`
          : job.status === "cancelled"
            ? " — cancelled"
            : " — complete";
      entries.push({
        id: id++,
        time: finishedAt,
        level,
        message: `${stageLabel(stage.key)}${suffix}`,
      });
    } else if (job.status === "running") {
      entries.push({
        id: id++,
        time: now,
        level: "info",
        message: `${stageLabel(stage.key)} is running…`,
      });
    }
  }
  return entries;
}

/** Newest `job:log` line for a stage, or the stage's own progress-derived line. */
export function toLogEntry(event: JobLogEvent, id: number, now: number): LogEntry {
  const level: LogLevel =
    event.level === "success" || event.level === "warn" || event.level === "error"
      ? event.level
      : "info";
  return { id, time: now, level, message: event.message };
}

/** Whether the console should auto-follow (sticky bottom) — true when at bottom. */
export function isAtBottom(container: HTMLElement | null, tolerancePx = 24): boolean {
  if (!container) return false;
  return container.scrollHeight - container.scrollTop - container.clientHeight <= tolerancePx;
}

export type TimelineItem = {
  key: StageKey;
  label: string;
  status: DerivedStageRun["status"];
  message: string | null;
  startedAt: number | null;
  finishedAt: number | null;
};

/** Vertical timeline: one row per plan stage with real job timing. */
export function buildTimeline(stages: DerivedStageRun[], jobs: Job[]): TimelineItem[] {
  const byId = new Map(jobs.map((j) => [j.id, j]));
  return stages.map((s) => {
    const job = s.jobId ? byId.get(s.jobId) : undefined;
    const startedAt = job?.started_at ? Date.parse(job.started_at) : null;
    const finishedAt = job?.finished_at ? Date.parse(job.finished_at) : null;
    const message = s.errorMessage ?? null;
    return {
      key: s.key,
      label: stageLabel(s.key),
      status: s.status,
      message,
      startedAt: Number.isNaN(startedAt as number) ? null : startedAt,
      finishedAt: Number.isNaN(finishedAt as number) ? null : finishedAt,
    };
  });
}
