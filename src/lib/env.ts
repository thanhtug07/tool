/**
 * Web-only mode — no desktop IPC.
 * Always returns false (Tauri has been removed).
 */
export function isTauri(): boolean {
  return false;
}
