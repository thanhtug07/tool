import { safeInvoke } from "@/api/invoke";

export type TaskStatus =
  "queued" | "ready" | "running" | "succeeded" | "failed" | "cancelled" | "blocked";
export type TaskType =
  "transcribe" | "translate" | "subtitle" | "tts" | "render" | "logo" | "chunk";

export type Task = {
  id: string;
  job_id: string;
  task_type: TaskType;
  stage: string;
  status: TaskStatus;
  progress: number;
  depends_on: string;
  params_json: string | null;
  input_fingerprint: string | null;
  result_json: string | null;
  error_code: string | null;
  error_message: string | null;
  retry_count: number;
  max_attempts: number;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type TaskStatusEvent = {
  jobId: string;
  taskId: string;
  taskType: string;
  status: TaskStatus;
  progress: number;
  error: { code: string; message: string } | null;
};

export type TaskProgressEvent = {
  jobId: string;
  taskId: string;
  taskType: string;
  progress: number;
  stage: string;
};

export type TaskLogEvent = {
  jobId: string;
  taskId: string | null;
  level: "info" | "success" | "warn" | "error";
  message: string;
};

export function listTasks(jobId: string): Promise<Task[]> {
  return safeInvoke("task.list", { jobId });
}

export function getTask(id: string): Promise<Task> {
  return safeInvoke("task.get", { id });
}
