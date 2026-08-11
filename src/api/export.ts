import { invoke } from "@tauri-apps/api/core";

/** QC verdict for an exported video (TASK-029). */
export type ExportQcReport = {
  passed: boolean;
  issues: string[];
  warnings: string[];
};

/** Outcome of exporting a rendered video to a user directory. */
export type ExportVideoResult = {
  path: string;
  qc: ExportQcReport;
};

/** Subtitle export format: SRT / VTT / ASS (ASS cannot be converted). */
export type SubtitleExportFormat = "srt" | "vtt" | "ass";

/**
 * Copies a rendered video into `targetDir` (auto-suffixed on collision) and
 * QC-checks it with ffprobe before reporting. Rejects with the worker's error
 * envelope (e.g. `E_PERMISSION_DENIED`, `E_DISK_FULL`, `E_EXPORT_QC`).
 */
export function exportVideo(
  sourceVideo: string,
  targetDir: string,
  options: { name?: string; runQc?: boolean } = {},
): Promise<ExportVideoResult> {
  return invoke("export.video", {
    sourceVideo,
    targetDir,
    name: options.name ?? null,
    runQc: options.runQc ?? null,
  });
}

/**
 * Exports a subtitle file into `targetDir`, optionally converting SRT↔VTT.
 * Returns the final written path.
 */
export function exportSubtitles(
  sourceSubtitle: string,
  targetDir: string,
  options: { name?: string; format?: SubtitleExportFormat } = {},
): Promise<string> {
  return invoke("export.subtitles", {
    sourceSubtitle,
    targetDir,
    name: options.name ?? null,
    format: options.format ?? null,
  });
}
