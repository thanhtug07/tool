import { safeInvoke } from "@/api/invoke";

/** Base URL of the worker HTTP API. */
const WORKER_BASE = "http://127.0.0.1:8765";

/**
 * Build a loadable URL for a local file path.
 *
 * In web mode this returns an `/api/media/stream?path=...` URL that the
 * worker serves with range-request support (HTTP 206) for video seeking.
 * If the path is already an HTTP URL it is returned as-is.
 */
export function toMediaUrl(path: string): string {
  const trimmed = path?.trim();
  if (!trimmed) return "";
  // Already an HTTP URL (e.g. from a previous stream or external source)
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  // Build the stream URL — encode the full OS path as a query param
  return `${WORKER_BASE}/api/media/stream?path=${encodeURIComponent(trimmed)}`;
}

/** Real ffprobe metadata for a local media file. */
export type MediaProbe = {
  duration: number;
  width: number;
  height: number;
  fps: number | null;
  audioTracks: number;
  videoCodec: string | null;
  container: string | null;
};

/** Probe a local media file with ffprobe through the backend. */
export function probeMedia(path: string): Promise<MediaProbe> {
  return safeInvoke("media.probe", { path });
}
