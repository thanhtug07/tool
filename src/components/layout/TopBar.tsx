import { Button } from "@/components/ui/button";
import { StatusDot, type StatusTone } from "@/components/ui/status";
import { MoreHorizontal, Save, Settings, Undo2, Redo2, X, Zap } from "lucide-react";
import { useState } from "react";

import { saveProject, type Project } from "@/api/project";
import { NAV_AREAS } from "@/lib/nav";
import type { NavKey } from "@/lib/nav";
import { workerStateLabel } from "@/lib/nav";
import { cn } from "@/components/ui/utils";
import { useToast } from "@/components/toast";
import { useJobs } from "@/stores/jobs";
import { useStudioStatus } from "@/stores/studio";
import { useWorker } from "@/stores/worker";

interface TopBarProps {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
  project: Project | null;
  onOpenProject: (project: Project | null) => void;
}

/**
 * Top bar — brand, nav, project, compact system status.
 * Secondary actions (undo/redo/save/export) live behind More.
 */
export default function TopBar({ active, onNavigate, project, onOpenProject }: TopBarProps) {
  const toast = useToast();
  const { info, hardware } = useWorker();
  const { projects } = useJobs();
  const pipeline = useStudioStatus();
  const worker = workerStateLabel(info?.state ?? "stopped");
  const [moreOpen, setMoreOpen] = useState(false);

  const running = pipeline.phase === "running";
  const pct = Math.round(pipeline.overallProgress * 100);
  const gpuReady = Boolean(hardware?.gpu_name || hardware?.gpu_vendor);

  const status: { label: string; tone: StatusTone } = !project
    ? { label: "No project", tone: "muted" }
    : pipeline.phase === "running"
      ? { label: "Processing", tone: "warn" }
      : pipeline.phase === "succeeded"
        ? { label: "Completed", tone: "ok" }
        : pipeline.phase === "failed"
          ? { label: "Failed", tone: "bad" }
          : { label: "Ready", tone: "ok" };

  async function handleSave() {
    if (!project) {
      toast.push("No project to save.", "info");
      return;
    }
    try {
      await saveProject(project.id);
      toast.push("Project saved.", "success");
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  return (
    <header
      data-role="top-bar"
      className="flex h-11 shrink-0 items-center gap-2 border-b border-border bg-panel px-3"
    >
      <div className="flex items-center gap-2 pr-1">
        <span className="grid size-6 shrink-0 place-items-center rounded bg-gold text-[10px] font-bold text-gold-foreground">
          AT
        </span>
        <p className="hidden text-[13px] font-semibold tracking-tight sm:block">AutoTranslate</p>
      </div>

      <span className="h-5 w-px bg-border" aria-hidden="true" />

      <select
        data-role="project-select"
        className="h-7 max-w-[180px] rounded border border-input bg-background px-2 text-xs text-foreground"
        value={project?.id ?? ""}
        onChange={(event) => {
          const id = event.target.value;
          const next = projects.find((p) => p.id === id) ?? null;
          onOpenProject(next);
        }}
        aria-label="Project"
      >
        <option value="">{project ? project.name : "No project"}</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>

      <nav aria-label="Workspace" className="flex items-center gap-0.5">
        {NAV_AREAS.map(({ key, label }) => {
          const selected = key === active;
          return (
            <button
              key={key}
              type="button"
              data-role={`nav-${key}`}
              aria-current={selected ? "page" : undefined}
              onClick={() => onNavigate(key)}
              className={cn(
                "rounded px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground",
                selected && "bg-accent text-accent-foreground",
              )}
            >
              {label}
            </button>
          );
        })}
      </nav>

      <span
        data-role="project-status"
        className="ml-1 hidden items-center gap-1.5 text-xs text-muted-foreground md:flex"
      >
        <StatusDot tone={status.tone} className="size-2" />
        {status.label}
        {running && <span className="tabular-nums">{pct}%</span>}
      </span>

      <div className="ml-auto flex items-center gap-2">
        <div className="hidden items-center gap-3 text-xs text-muted-foreground xl:flex">
          <span className="flex items-center gap-1.5" data-role="worker-status">
            <StatusDot tone={worker.tone} className="size-2" />
            Worker
          </span>
          <span data-role="gpu-status" className="flex items-center gap-1.5">
            <StatusDot tone={gpuReady ? "ok" : "muted"} className="size-2" />
            GPU
          </span>
        </div>

        {running && (
          <Button
            variant="outline"
            size="sm"
            data-role="cancel-automation"
            onClick={() => pipeline.cancel?.()}
          >
            <X className="size-3.5" aria-hidden="true" /> Cancel
          </Button>
        )}

        {pipeline.canExport && !running && (
          <Button
            type="button"
            size="sm"
            data-role="export-button"
            onClick={() => pipeline.export?.()}
            className="bg-gold text-gold-foreground hover:bg-gold/90"
          >
            <Zap className="size-3.5" aria-hidden="true" /> Export
          </Button>
        )}

        {!pipeline.canExport && (
          <span data-role="export-button" className="sr-only">
            Export Video
          </span>
        )}

        <div className="relative">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            data-role="more-menu"
            onClick={() => setMoreOpen((o) => !o)}
            aria-label="More"
          >
            <MoreHorizontal className="size-3.5" aria-hidden="true" />
          </Button>
          {moreOpen && (
            <>
              <div
                className="fixed inset-0 z-20"
                onClick={() => setMoreOpen(false)}
                aria-hidden="true"
              />
              <div className="absolute right-0 top-full z-30 mt-1 w-44 rounded-md border border-border bg-card p-1 shadow-lg">
                <MoreItem
                  label="Undo"
                  icon={Undo2}
                  disabled={!pipeline.undo}
                  onClick={() => {
                    pipeline.undo?.();
                    setMoreOpen(false);
                  }}
                  role="undo"
                />
                <MoreItem
                  label="Redo"
                  icon={Redo2}
                  disabled={!pipeline.redo}
                  onClick={() => {
                    pipeline.redo?.();
                    setMoreOpen(false);
                  }}
                  role="redo"
                />
                <MoreItem
                  label="Save"
                  icon={Save}
                  onClick={() => {
                    void handleSave();
                    setMoreOpen(false);
                  }}
                  role="save-project"
                />
                <MoreItem
                  label="Settings"
                  icon={Settings}
                  onClick={() => {
                    onNavigate("settings");
                    setMoreOpen(false);
                  }}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

function MoreItem({
  label,
  icon: Icon,
  onClick,
  disabled,
  role,
}: {
  label: string;
  icon: typeof Save;
  onClick: () => void;
  disabled?: boolean;
  role?: string;
}) {
  return (
    <button
      type="button"
      data-role={role}
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </button>
  );
}
