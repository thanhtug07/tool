import { describe, expect, it } from "vitest";

import type { Job } from "@/api/job";
import {
  DEFAULT_MAX_LOG_ENTRIES,
  appendLogEntry,
  backfillFromJobs,
  buildTimeline,
  computeEta,
  formatEta,
  stageLabel,
  toLogEntry,
  type LogEntry,
} from "./logHelpers";

const JOB = (over: Partial<Job> = {}): Job => ({
  id: "job_1",
  project_id: "p",
  type: "transcribe",
  status: "succeeded",
  progress: 1,
  stage: "done",
  error_code: null,
  error_message: null,
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:05Z",
  finished_at: "2026-01-01T00:00:45Z",
  ...over,
});

describe("appendLogEntry", () => {
  it("caps at the maximum and keeps the newest lines", () => {
    let entries: LogEntry[] = [];
    for (let i = 0; i < DEFAULT_MAX_LOG_ENTRIES + 50; i++) {
      entries = appendLogEntry(
        entries,
        { id: i, time: i, level: "info", message: `m${i}` },
        DEFAULT_MAX_LOG_ENTRIES,
      );
    }
    expect(entries).toHaveLength(DEFAULT_MAX_LOG_ENTRIES);
    expect(entries[0].message).toBe("m50");
    expect(entries[entries.length - 1].message).toBe(`m${DEFAULT_MAX_LOG_ENTRIES + 49}`);
  });

  it("keeps zero entries empty", () => {
    expect(appendLogEntry([], { id: 1, time: 0, level: "info", message: "x" }, 0)).toEqual([]);
  });
});

describe("computeEta", () => {
  it("hides ETA before meaningful progress", () => {
    expect(computeEta(0.01, 60_000)).toBeNull();
    expect(computeEta(0, 60_000)).toBeNull();
  });

  it("hides ETA after completion", () => {
    expect(computeEta(1, 60_000)).toBeNull();
  });

  it("computes ETA from real progress velocity", () => {
    // 2 min elapsed at 50% → 2 min remaining.
    expect(computeEta(0.5, 120_000)).toBe(120_000);
    // 3 min elapsed at 75% → 1 min remaining.
    expect(computeEta(0.75, 180_000)).toBe(60_000);
  });

  it("returns null on invalid input", () => {
    expect(computeEta(Number.NaN, 1000)).toBeNull();
    expect(computeEta(0.5, -1)).toBeNull();
  });
});

describe("formatEta", () => {
  it("formats mm:ss", () => {
    expect(formatEta(87_000)).toBe("01:27");
    expect(formatEta(0)).toBe("00:00");
  });
});

describe("stageLabel", () => {
  it("maps pipeline keys to labels", () => {
    expect(stageLabel("transcribe")).toBe("Transcription");
    expect(stageLabel("tts")).toBe("Voice generation");
  });
});

describe("toLogEntry", () => {
  it("maps a job:log event preserving level", () => {
    const e = toLogEntry({ jobId: "j", level: "success", message: "done" }, 7, 123);
    expect(e).toEqual({ id: 7, time: 123, level: "success", message: "done" });
  });
});

describe("backfillFromJobs", () => {
  it("restores stage history from persisted jobs", () => {
    const planStages = [
      { key: "transcribe" as const, jobId: "job_1" },
      { key: "translate" as const, jobId: "job_2" },
    ];
    const jobs = [
      JOB({ id: "job_1", started_at: "2026-01-01T00:00:05Z", finished_at: "2026-01-01T00:00:45Z" }),
      JOB({
        id: "job_2",
        type: "translate",
        status: "failed",
        started_at: "2026-01-01T00:00:46Z",
        finished_at: "2026-01-01T00:01:10Z",
        error_code: "E_PROVIDER_UNAVAILABLE",
        error_message: "provider down",
      }),
    ];
    const entries = backfillFromJobs(planStages, jobs, 1_000_000);
    expect(entries.map((e) => e.message)).toEqual([
      "Transcription started",
      "Transcription — complete",
      "Translation started",
      "Translation — provider down",
    ]);
    expect(entries[1].level).toBe("success");
    expect(entries[3].level).toBe("error");
  });

  it("skips stages with no job row", () => {
    expect(backfillFromJobs([{ key: "render" as const, jobId: "missing" }], [], 0)).toEqual([]);
  });
});

describe("buildTimeline", () => {
  it("attaches real job timing to each stage", () => {
    const stages = [
      {
        key: "transcribe" as const,
        jobId: "job_1",
        status: "succeeded" as const,
        progress: 1,
        stage: "done",
        errorCode: null,
        errorMessage: null,
      },
    ];
    const items = buildTimeline(stages, [JOB()]);
    expect(items[0].label).toBe("Transcription");
    expect(items[0].startedAt).toBe(Date.parse("2026-01-01T00:00:05Z"));
    expect(items[0].finishedAt).toBe(Date.parse("2026-01-01T00:00:45Z"));
  });
});
