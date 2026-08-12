import { safeInvoke } from "@/api/invoke";

/** Lifecycle state of the Python sidecar (mirrors Rust `WorkerState`). */
export type WorkerState = "stopped" | "starting" | "ready" | "stopping" | "failed";

/** Snapshot exposed over IPC (never contains the session token). */
export type WorkerStateInfo = {
  state: WorkerState;
  pid: number | null;
  port: number | null;
  restarts: number;
  last_error: string | null;
};

/** Current lifecycle snapshot of the Python sidecar. */
export function getWorkerState(): Promise<WorkerStateInfo> {
  return safeInvoke("worker.get_worker_state");
}

/** Stop and restart the Python sidecar (fresh port + session token). */
export function restartWorker(): Promise<WorkerStateInfo> {
  return safeInvoke("worker.restart");
}
