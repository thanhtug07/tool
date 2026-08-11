import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

vi.mock("@/api/events", () => ({
  onJobStatus: vi.fn().mockResolvedValue(() => {}),
}));

vi.mock("@/api/job", () => ({
  submitJob: vi.fn(),
  listJobs: vi.fn().mockResolvedValue([]),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
}));

vi.mock("@/api/pipeline", () => ({
  getArtifactPaths: vi.fn().mockResolvedValue({
    projectDir: "C:\\data\\projects\\p",
    audio: "C:\\data\\projects\\p\\cache\\audio.wav",
    transcript: "C:\\data\\projects\\p\\cache\\transcript.json",
    translation: "C:\\data\\projects\\p\\cache\\translation.json",
    subtitleSrt: "C:\\data\\projects\\p\\cache\\subtitle.srt",
    subtitleAss: "C:\\data\\projects\\p\\cache\\subtitle.ass",
    renderedVideo: "C:\\data\\projects\\p\\output\\rendered.mp4",
  }),
}));

vi.mock("@/api/project", () => ({
  createProject: vi.fn(),
  deleteProject: vi.fn(),
  listProjects: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/api/export", () => ({
  exportVideo: vi.fn(),
  exportSubtitles: vi.fn(),
}));

import {
  buildStageParams,
  canRunStage,
  latestJob,
  PIPELINE_STAGES,
  statusLabel,
  default as ProjectsPage,
} from "./index";
import type { Project } from "@/api/project";
import type { Job } from "@/api/job";

const PROJECT: Project = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Sample",
  source_video_path: "C:\\Videos\\sample.mp4",
  status: "draft",
  created_at: "2026-08-01T00:00:00.000Z",
  updated_at: "2026-08-01T00:00:00.000Z",
};

function job(type: string, status: Job["status"], created_at = "2026-08-01T00:00:00.000Z"): Job {
  return {
    id: `job_${type}`,
    project_id: PROJECT.id,
    type,
    status,
    progress: status === "succeeded" ? 1 : 0.5,
    stage: status === "running" ? "working" : "done",
    error_code: null,
    error_message: null,
    created_at,
    started_at: null,
    finished_at: null,
  };
}

describe("pipeline helpers (pure)", () => {
  it("builds stage params for each pipeline stage", () => {
    const options = { provider: "gemini", targetLanguage: "zh" };
    expect(buildStageParams("transcribe", PROJECT, options)).toEqual({
      video_path: "C:\\Videos\\sample.mp4",
    });
    expect(buildStageParams("translate", PROJECT, options)).toEqual({
      provider: "gemini",
      target_language: "zh",
    });
    expect(buildStageParams("subtitle", PROJECT, options)).toEqual({});
    expect(buildStageParams("render", PROJECT, options)).toEqual({});
  });

  it("enumerates the pipeline stages in order", () => {
    expect(PIPELINE_STAGES.map((s) => s.type)).toEqual([
      "transcribe",
      "translate",
      "subtitle",
      "render",
    ]);
  });

  it("finds the latest job of a stage type", () => {
    const jobs = [job("transcribe", "failed"), job("transcribe", "succeeded")];
    const latest = latestJob(jobs, "transcribe");
    expect(latest?.status).toBe("succeeded");
    expect(latestJob(jobs, "render")).toBeNull();
  });

  it("gates stages on the previous stage succeeding", () => {
    expect(canRunStage("transcribe", [], PROJECT)).toBe(true);
    expect(canRunStage("transcribe", [], { ...PROJECT, source_video_path: " " })).toBe(false);
    expect(canRunStage("translate", [job("transcribe", "succeeded")], PROJECT)).toBe(true);
    expect(canRunStage("translate", [job("transcribe", "failed")], PROJECT)).toBe(false);
    expect(canRunStage("subtitle", [job("translate", "succeeded")], PROJECT)).toBe(true);
    expect(canRunStage("render", [job("subtitle", "succeeded")], PROJECT)).toBe(true);
    expect(canRunStage("render", [], PROJECT)).toBe(false);
  });

  it("labels job statuses", () => {
    expect(statusLabel("queued")).toBe("Queued");
    expect(statusLabel("running")).toBe("Running");
    expect(statusLabel("succeeded")).toBe("Succeeded");
    expect(statusLabel("failed")).toBe("Failed");
    expect(statusLabel("cancelled")).toBe("Cancelled");
  });
});

describe("ProjectsPage (unit — mocked bridge, no Tauri IPC)", () => {
  it("renders the import form and empty project list", () => {
    const html = renderToStaticMarkup(<ProjectsPage />);
    expect(html).toContain("Projects");
    expect(html).toContain("New project");
    expect(html).toContain("Browse…");
    expect(html).toContain("Create project");
    expect(html).toContain("No projects yet.");
  });

  it("renders no pipeline panel without a selected project", () => {
    const html = renderToStaticMarkup(<ProjectsPage />);
    expect(html).not.toContain("Run Transcribe");
  });
});
