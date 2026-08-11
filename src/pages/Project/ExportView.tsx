import { useState } from "react";

import {
  exportSubtitles,
  exportVideo,
  type ExportQcReport,
  type ExportVideoResult,
  type SubtitleExportFormat,
} from "@/api/export";

export const SUBTITLE_FORMATS: SubtitleExportFormat[] = ["srt", "vtt", "ass"];

/** Human-readable QC verdict line, e.g. "Passed" / "Failed — 2 issues". */
export function qcSummary(qc: ExportQcReport): string {
  if (qc.passed) {
    return qc.warnings.length > 0
      ? `Passed (${qc.warnings.length} warning${qc.warnings.length === 1 ? "" : "s"})`
      : "Passed";
  }
  return `Failed — ${qc.issues.length} issue${qc.issues.length === 1 ? "" : "s"}`;
}

/**
 * Export page (TASK-029): copy the rendered video (with ffprobe QC) and/or
 * subtitle files into a user-chosen directory. Names are auto-suffixed on
 * collision; errors surface the architecture code (e.g. E_PERMISSION_DENIED).
 */
export default function ExportView() {
  const [videoPath, setVideoPath] = useState("");
  const [subtitlePath, setSubtitlePath] = useState("");
  const [targetDir, setTargetDir] = useState("");
  const [videoName, setVideoName] = useState("");
  const [runQc, setRunQc] = useState(true);
  const [subtitleFormat, setSubtitleFormat] = useState<SubtitleExportFormat>("srt");
  const [result, setResult] = useState<ExportVideoResult | null>(null);
  const [subtitleResult, setSubtitleResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function exportVideoFile() {
    setError(null);
    setResult(null);
    setBusy(true);
    try {
      const exported = await exportVideo(videoPath.trim(), targetDir.trim(), {
        name: videoName.trim() || undefined,
        runQc,
      });
      setResult(exported);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function exportSubtitleFile() {
    setError(null);
    setSubtitleResult(null);
    setBusy(true);
    try {
      const path = await exportSubtitles(subtitlePath.trim(), targetDir.trim(), {
        format: subtitleFormat,
      });
      setSubtitleResult(path);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="export-heading" className="space-y-4">
      <h1 id="export-heading" className="text-lg font-semibold">
        Export
      </h1>
      <p className="text-sm text-muted-foreground">
        Copy the rendered video (QC-checked with ffprobe) and subtitle files into a folder of your
        choice. Existing files get an automatic &ldquo; (1)&rdquo; suffix.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="export-target-dir" className="text-sm">
          Target folder
        </label>
        <input
          id="export-target-dir"
          data-role="export-target-dir"
          className="w-80 rounded border border-border bg-background px-2 py-1 text-sm"
          placeholder="C:\Exports"
          value={targetDir}
          onChange={(event) => setTargetDir(event.target.value)}
        />
      </div>

      <div className="space-y-3 rounded border border-border p-3">
        <h2 className="text-sm font-medium">Video</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="export-video-path" className="text-sm">
            Rendered video
          </label>
          <input
            id="export-video-path"
            data-role="export-video-path"
            className="w-80 rounded border border-border bg-background px-2 py-1 text-sm"
            placeholder="C:\render\output.mp4"
            value={videoPath}
            onChange={(event) => setVideoPath(event.target.value)}
          />
          <label htmlFor="export-video-name" className="text-sm">
            File name (optional)
          </label>
          <input
            id="export-video-name"
            className="w-48 rounded border border-border bg-background px-2 py-1 text-sm"
            placeholder="my-video"
            value={videoName}
            onChange={(event) => setVideoName(event.target.value)}
          />
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={runQc}
              onChange={(event) => setRunQc(event.target.checked)}
            />
            QC check (ffprobe)
          </label>
          <button
            type="button"
            data-role="export-video-button"
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            disabled={busy || !videoPath.trim() || !targetDir.trim()}
            onClick={() => void exportVideoFile()}
          >
            {busy ? "Exporting…" : "Export video"}
          </button>
        </div>
        {result && (
          <p data-role="export-video-result" className="text-sm">
            <span className="font-medium">{result.path}</span> — QC: {qcSummary(result.qc)}
            {result.qc.warnings.length > 0 && (
              <span className="block text-muted-foreground">{result.qc.warnings.join("; ")}</span>
            )}
          </p>
        )}
      </div>

      <div className="space-y-3 rounded border border-border p-3">
        <h2 className="text-sm font-medium">Subtitles</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="export-subtitle-path" className="text-sm">
            Subtitle file
          </label>
          <input
            id="export-subtitle-path"
            data-role="export-subtitle-path"
            className="w-80 rounded border border-border bg-background px-2 py-1 text-sm"
            placeholder="C:\subs\subtitle.srt"
            value={subtitlePath}
            onChange={(event) => setSubtitlePath(event.target.value)}
          />
          <label htmlFor="export-subtitle-format" className="text-sm">
            Format
          </label>
          <select
            id="export-subtitle-format"
            className="rounded border border-border bg-background px-2 py-1 text-sm"
            value={subtitleFormat}
            onChange={(event) => setSubtitleFormat(event.target.value as SubtitleExportFormat)}
          >
            {SUBTITLE_FORMATS.map((format) => (
              <option key={format} value={format}>
                {format.toUpperCase()}
              </option>
            ))}
          </select>
          <button
            type="button"
            data-role="export-subtitle-button"
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            disabled={busy || !subtitlePath.trim() || !targetDir.trim()}
            onClick={() => void exportSubtitleFile()}
          >
            Export subtitles
          </button>
        </div>
        {subtitleResult && (
          <p data-role="export-subtitle-result" className="text-sm">
            <span className="font-medium">{subtitleResult}</span>
          </p>
        )}
      </div>

      {error && (
        <p data-role="export-error" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
