import { invoke } from "@tauri-apps/api/core";

import { isTauri } from "@/lib/env";

/**
 * `invoke()` wrapper for every api module.
 *
 * Outside the Tauri webview — plain browser dev server, the Preview tab,
 * unit tests — `window.__TAURI_INTERNALS__` is absent and `invoke()` rejects
 * with the cryptic `TypeError: Cannot read properties of undefined (reading
 * 'invoke')`. This wrapper converts that into a clear, catchable error so
 * callers (stores, pages) degrade gracefully instead of crashing.
 *
 * Inside Tauri the behavior is identical to `invoke()` — errors pass through
 * untouched.
 */
export async function safeInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await (args === undefined ? invoke<T>(cmd) : invoke<T>(cmd, args));
  } catch (error) {
    if (!isTauri()) {
      throw new Error(
        `Cannot reach the app core (command "${cmd}"): this page is running outside the Tauri window. Run the app with \`npm run tauri dev\`.`,
        { cause: error },
      );
    }
    throw error;
  }
}
