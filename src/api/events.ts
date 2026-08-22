import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { isTauri } from "@/lib/env";
import type { JobLogEvent, JobStatusEvent } from "./job";
import type { ModelDownloadProgress } from "./models";
import type { TaskProgressEvent, TaskStatusEvent } from "./task";

/**
 * Subscribe to `job:status` events emitted by the Rust JobService.
 * Returns an `unlisten` function for cleanup.
 *
 * Outside the Tauri runtime (plain browser dev / preview / tests) there is no
 * event bridge, so this resolves to a no-op instead of throwing.
 */
export function onJobStatus(handler: (event: JobStatusEvent) => void): Promise<UnlistenFn> {
  if (!isTauri()) {
    return Promise.resolve(() => {});
  }
  return listen<JobStatusEvent>("job:status", (event) => handler(event.payload));
}

/**
 * Subscribe to `job:log` events (live-log console lines) emitted by the Rust
 * JobService / pipeline runner. No-op outside the Tauri runtime.
 */
export function onJobLog(handler: (event: JobLogEvent) => void): Promise<UnlistenFn> {
  if (!isTauri()) {
    return Promise.resolve(() => {});
  }
  return listen<JobLogEvent>("job:log", (event) => handler(event.payload));
}

/**
 * Subscribe to `models:download-progress` events emitted while a translation
 * GGUF downloads (Settings → Providers → Local LLM). No-op outside Tauri.
 */
export function onModelDownloadProgress(
  handler: (event: ModelDownloadProgress) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) {
    return Promise.resolve(() => {});
  }
  return listen<ModelDownloadProgress>("models:download-progress", (event) =>
    handler(event.payload),
  );
}

export function onTaskStatus(handler: (event: TaskStatusEvent) => void): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(() => {});
  return listen<TaskStatusEvent>("task:status", (e) => handler(e.payload));
}

export function onTaskProgress(handler: (event: TaskProgressEvent) => void): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(() => {});
  return listen<TaskProgressEvent>("task:progress", (e) => handler(e.payload));
}

export function onTaskLog(
  handler: (event: import("./task").TaskLogEvent) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return Promise.resolve(() => {});
  return listen<import("./task").TaskLogEvent>("task:log", (e) => handler(e.payload));
}
