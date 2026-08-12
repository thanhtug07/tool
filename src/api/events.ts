import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { isTauri } from "@/lib/env";
import type { JobStatusEvent } from "./job";

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
