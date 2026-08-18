import { useState } from "react";
import {
  ChevronDown,
  FileVideo,
  FolderOpen,
  Play,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
} from "lucide-react";

import type { Project } from "@/api/project";
import type { ArtifactPaths } from "@/api/pipeline";
import { Button } from "@/components/ui/button";
import { fileBaseName, formatDuration } from "@/lib/format";
import ConfirmDialog from "./ConfirmDialog";
import type { VideoMeta } from "./types";

/**
 * CUSTOM header — page title on the left, a single [+ Action] dropdown with
 * the workspace actions, and the project/video status on the right.
 */
export default function CustomHeader({
  project,
  meta,
  artifacts,
  resultReady,
  onOpenVideo,
  onOpenOutputFolder,
  onPreviewResult,
  onResetTools,
  onClearProject,
}: {
  project: Project | null;
  meta: VideoMeta | null;
  artifacts: ArtifactPaths | null;
  resultReady: boolean;
  onOpenVideo: () => void;
  onOpenOutputFolder: () => void;
  /** Show the rendered output in the Result preview tab and play it. */
  onPreviewResult: () => void;
  onResetTools: () => void;
  onClearProject: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<null | "reset" | "clear">(null);
  const outputReady = resultReady && Boolean(artifacts);

  const actions: { label: string; icon: typeof Plus; onClick: () => void }[] = [
    { label: "Reset Current Tool", icon: RotateCcw, onClick: () => setConfirming("reset") },
    { label: "Clear Project", icon: Trash2, onClick: () => setConfirming("clear") },
  ];

  return (
    <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-panel px-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        Custom
      </p>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onOpenVideo}
          data-role="custom-replace-video"
        >
          {project ? (
            <>
              <FileVideo className="size-3.5" aria-hidden="true" /> Replace
            </>
          ) : (
            <>
              <Upload className="size-3.5" aria-hidden="true" /> Open Video
            </>
          )}
        </Button>
        {outputReady && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onPreviewResult}
            data-role="custom-output-video"
            title={artifacts?.renderedVideo}
            className="max-w-[220px] gap-1.5 text-gold"
          >
            <Play className="size-3.5 shrink-0 fill-current" aria-hidden="true" />
            <span className="truncate">{fileBaseName(artifacts?.renderedVideo ?? "")}</span>
          </Button>
        )}
        {project && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!outputReady}
            onClick={onOpenOutputFolder}
            data-role="custom-output"
            title={outputReady ? artifacts?.renderedVideo : "Output is not ready yet"}
          >
            <FolderOpen className="size-3.5" aria-hidden="true" /> Output
          </Button>
        )}
      </div>

      {/* [+ Action] dropdown */}
      <div className="relative">
        <Button
          type="button"
          data-role="custom-action"
          size="sm"
          variant="outline"
          onClick={() => setOpen((o) => !o)}
        >
          <Plus className="size-3.5" aria-hidden="true" /> Action
          <ChevronDown className="size-3.5" aria-hidden="true" />
        </Button>
        {open && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} aria-hidden="true" />
            <div
              data-role="custom-action-menu"
              className="absolute left-0 top-full z-30 mt-1 w-56 rounded-md border border-border bg-card p-1 shadow-lg"
            >
              {actions.map((action) => {
                const Icon = action.icon;
                return (
                  <button
                    key={action.label}
                    type="button"
                    data-role={`action-${action.label.toLowerCase().replaceAll(" ", "-")}`}
                    onClick={() => {
                      setOpen(false);
                      action.onClick();
                    }}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                  >
                    <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                    {action.label}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* Project / video status */}
      <div className="ml-auto flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground">
        {project ? (
          <>
            <Upload className="size-3 shrink-0" aria-hidden="true" />
            <span
              className="truncate font-medium text-foreground"
              title={project.source_video_path}
            >
              {fileBaseName(project.source_video_path)}
            </span>
            {meta && meta.width > 0 && (
              <span className="shrink-0 tabular-nums">
                {meta.width}×{meta.height}
                {meta.fps ? ` · ${meta.fps} fps` : ""}
              </span>
            )}
            {meta && meta.duration > 0 && (
              <span className="shrink-0 tabular-nums">{formatDuration(meta.duration)}</span>
            )}
          </>
        ) : (
          <span>No project — open a video to start.</span>
        )}
        {outputReady && (
          <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
            output ready
          </span>
        )}
      </div>

      <ConfirmDialog
        open={confirming === "reset"}
        title="Reset current tool?"
        message="Remove every configured tool from this workspace. The video and project stay untouched."
        confirmLabel="Reset tools"
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          onResetTools();
          setConfirming(null);
        }}
      />
      <ConfirmDialog
        open={confirming === "clear"}
        title="Clear project?"
        message={
          project
            ? `Delete project “${project.name}” and its working files? This cannot be undone.`
            : "Delete this project and its working files? This cannot be undone."
        }
        confirmLabel="Delete project"
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          onClearProject();
          setConfirming(null);
        }}
      />
    </div>
  );
}
