import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type { JobStatusEvent } from "./job";

/**
 * Subscribe to `job:status` events emitted by the Rust JobService.
 * Returns an `unlisten` function for cleanup.
 */
export function onJobStatus(handler: (event: JobStatusEvent) => void): Promise<UnlistenFn> {
  return listen<JobStatusEvent>("job:status", (event) => handler(event.payload));
}
