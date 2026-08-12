/**
 * True when running inside the Tauri webview (IPC + event bridge available).
 * Outside Tauri — plain browser dev server, preview, tests — IPC calls would
 * throw, so callers skip them and fall back to safe defaults.
 */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
