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
      className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-card px-4 shadow-[var(--shadow-xs)]"
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 pr-2">
        <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-bold text-accent-foreground">
          AT
        </span>
        <p className="hidden text-sm font-semibold tracking-tight text-foreground sm:block">
          AutoTranslate
        </p>
      </div>

      <span className="h-5 w-px bg-border" aria-hidden="true" />

      {/* Project Selector */}
      <select
        data-role="project-select"
        className="h-8 max-w-[200px] rounded-md border border-border bg-background px-3 text-xs font-medium text-foreground transition-colors hover:border-border-strong focus:outline-2 focus:outline-offset-1 focus:outline-[var(--accent)]"
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

      {/* Navigation */}
      <nav aria-label="Workspace" className="flex items-center gap-1">
        {NAV_AREAS.map(({ key, label }) => {
          const selected = key === active;
          return (
            <button
              key={key}
              type="button"
              data-role={"nav-" + key}
              aria-current={selected ? "page" : undefined}
              onClick={() => onNavigate(key)}
              className={cn(
                "relative rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                selected
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
              )}
            >
              {label}
              {selected && (
                <span className="absolute -bottom-[9px] left-1/2 h-0.5 w-4 -translate-x-1/2 rounded-full bg-accent" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Status */}
      <span
        data-role="project-status"
        className="ml-1 hidden items-center gap-1.5 text-xs text-muted-foreground md:flex"
      >
        <StatusDot tone={status.tone} className="size-2" />
        {status.label}
        {running && <span className="tabular-nums font-medium">{pct}%</span>}
      </span>

      {/* Right Actions */}
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
            className="bg-accent text-accent-foreground hover:bg-accent/90"
          >
            <Zap className="size-3.5" aria-hidden="true" /> Export
          </Button>
        )}

        {!pipeline.canExport && (
          <span data-role="export-button" className="sr-only">
            Export Video
          </span>
        )}

        {/* More Menu */}
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
              <div className="absolute right-0 top-full z-30 mt-1 w-48 rounded-lg border border-border bg-card p-1.5 shadow-[var(--shadow-md)]">
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
                <div className="my-1 h-px bg-border" />
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
      className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {label}
    </button>
  );
}
