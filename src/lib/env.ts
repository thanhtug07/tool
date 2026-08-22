/**
 * Always returns false in web-only mode (Vite localhost environment).
 */
export function isTauri(): boolean {
  return false;
}
