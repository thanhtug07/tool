import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import { cancelJob, listJobs, retryJob, submitJob, type Job } from "./job";

const mockedInvoke = vi.mocked(invoke);

const JOB: Job = {
  id: "job_0001",
  project_id: "00000000-0000-4000-8000-000000000001",
  type: "transcribe",
  status: "queued",
  progress: 0,
  stage: "queued",
  error_code: null,
  error_message: null,
  created_at: "2026-08-01T00:00:00.000Z",
  started_at: null,
  finished_at: null,
};

describe("job bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("submits a job with stage params", async () => {
    mockedInvoke.mockResolvedValue(JOB);
    const job = await submitJob(JOB.project_id, "transcribe", { video_path: "C:\\v.mp4" });
    expect(mockedInvoke).toHaveBeenCalledWith("job.submit", {
      projectId: JOB.project_id,
      jobType: "transcribe",
      params: { video_path: "C:\\v.mp4" },
    });
    expect(job.status).toBe("queued");
  });

  it("lists jobs for a project", async () => {
    mockedInvoke.mockResolvedValue([JOB]);
    const jobs = await listJobs(JOB.project_id);
    expect(mockedInvoke).toHaveBeenCalledWith("job.list", { projectId: JOB.project_id });
    expect(jobs).toHaveLength(1);
  });

  it("cancels a job", async () => {
    mockedInvoke.mockResolvedValue(undefined);
    await cancelJob("job_0001");
    expect(mockedInvoke).toHaveBeenCalledWith("job.cancel", { id: "job_0001" });
  });

  it("retries a job", async () => {
    mockedInvoke.mockResolvedValue(undefined);
    await retryJob("job_0001");
    expect(mockedInvoke).toHaveBeenCalledWith("job.retry", { id: "job_0001" });
  });
});
