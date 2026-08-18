import { Check, Copy, FolderOpen, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fileBaseName, formatDuration, formatProcessingTime } from "@/lib/format";
import type { ArtifactPaths } from "@/api/pipeline";
import type { VideoMeta } from "./types";

/**
 * RESULT SUMMARY — the completion card shown above the live log after a Custom
 * run finishes: input → output, video duration, real processing time, and the
 * three actions (preview the result, open the output folder, copy the path).
 */
export default function ResultCard({
  inputPath,
  artifacts,
  meta,
  processingMs,
  onPreview,
  onOpenFolder,
  onCopyPath,
}: {
  inputPath: string;
  artifacts: ArtifactPaths;
  meta: VideoMeta | null;
  processingMs: number | null;
  onPreview: () => void;
  onOpenFolder: () => void;
  onCopyPath: () => void;
}) {
  return (
    <div data-role="custom-result" className="shrink-0 border-t border-border bg-panel">
      <div className="flex items-start gap-3 p-3">
        <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-emerald-500/15">
          <Check className="size-3.5 text-emerald-400" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Completed</p>
          <div className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className="text-muted-foreground/70">Input</span>
            <span className="min-w-0 truncate font-medium text-foreground" title={inputPath}>
              {fileBaseName(inputPath)}
            </span>
            <span className="text-muted-foreground/70">Output</span>
            <span
              className="min-w-0 truncate font-medium text-foreground"
              title={artifacts.renderedVideo}
            >
              {fileBaseName(artifacts.renderedVideo)}
            </span>
            <span className="text-muted-foreground/70">Duration</span>
            <span className="tabular-nums">
              {meta && meta.duration > 0 ? formatDuration(meta.duration) : "—"}
            </span>
            <span className="text-muted-foreground/70">Processing</span>
            <span className="tabular-nums">
              {processingMs !== null ? formatProcessingTime(processingMs) : "—"}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col gap-1.5">
          <Button type="button" size="sm" data-role="result-preview" onClick={onPreview}>
            <Play className="size-3.5" aria-hidden="true" /> Preview Result
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-role="result-open-folder"
            onClick={onOpenFolder}
          >
            <FolderOpen className="size-3.5" aria-hidden="true" /> Open Output Folder
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            data-role="result-copy-path"
            onClick={onCopyPath}
          >
            <Copy className="size-3.5" aria-hidden="true" /> Copy Path
          </Button>
        </div>
      </div>
    </div>
  );
}
