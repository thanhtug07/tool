import { convertFileSrc } from "@tauri-apps/api/core";

import { safeInvoke } from "@/api/invoke";

export const MEDIA_SCHEME = "media";

/**
 * Build a loadable URL for an absolute local file path.
 *
 * Inside the Tauri shell this uses the official `asset://` protocol
 * (enabled + runtime-scoped in Rust), which is the supported way to stream
 * local video in WebView2 — custom schemes are not reliably served to the
 * media pipeline. The Rust core restricts the asset scope to registered
 * project source videos and project working directories, so arbitrary local
 * files are still refused.
 *
 * Outside the Tauri shell (e.g. the browser dev preview) there is no asset
 * protocol; the raw path is returned so the player attempts it and reports
 * an honest load error instead of throwing.
 */
export function toMediaUrl(path: string): string {
  const trimmed = path?.trim() ?? "";
  if (!trimmed) return "";
  const hasTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  if (!hasTauri) return trimmed;
  return convertFileSrc(trimmed);
}

/** Real ffprobe metadata for a local media file (Rust `media.probe`). */
export type MediaProbe = {
  duration: number;
  width: number;
  height: number;
  fps: number | null;
  audioTracks: number;
  videoCodec: string | null;
  container: string | null;
};

/**
 * Probe a local media file with ffprobe through the Rust core. The path must
 * be a registered project source video or a file inside a project working
 * directory; anything else is refused by the backend.
 */
export function probeMedia(path: string): Promise<MediaProbe> {
  return safeInvoke("media.probe", { path });
}
