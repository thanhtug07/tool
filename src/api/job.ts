import { invoke } from "@tauri-apps/api/core";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

/** Canonical Job row (subset used by the UI). */
export type Job = {
  id: string;
  project_id: string;
  type: string;
  status: JobStatus;
  progress: number;
  stage: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

/** `job:status` event payload (MASTER_PLAN.md §25.2). */
export type JobStatusEvent = {
  jobId: string;
  status: JobStatus;
  progress: number;
  stage: string;
  error: { code: string; message: string } | null;
};

export function getJob(id: string): Promise<Job> {
  return invoke("job.get", { id });
}

/** Submit a pipeline job for a project (RELEASE-P0-005 workflow). */
export function submitJob(
  projectId: string,
  type: string,
  params: Record<string, unknown>,
): Promise<Job> {
  return invoke("job.submit", { projectId, jobType: type, params });
}

/** All jobs for a project, most recently updated first. */
export function listJobs(projectId: string): Promise<Job[]> {
  return invoke("job.list", { projectId });
}

/**
 * All jobs across every project, most recently updated first — the Dashboard
 * feed and the single source of truth for "current job".
 */
export function listAllJobs(limit?: number): Promise<Job[]> {
  return invoke("job.list_all", { limit: limit ?? null });
}

/** Cancel a queued or running job. */
export function cancelJob(id: string): Promise<void> {
  return invoke("job.cancel", { id });
}

export function retryJob(id: string): Promise<void> {
  return invoke("job.retry", { id });
}
