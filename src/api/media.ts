import { safeInvoke } from "@/api/invoke";

/**
 * Build a loadable URL for a local file path or web media URL.
 */
export function toMediaUrl(path: string): string {
  const trimmed = path?.trim() ?? "";
  return trimmed;
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
