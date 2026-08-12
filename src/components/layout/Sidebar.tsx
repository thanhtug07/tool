import { LayoutDashboard, Settings, Wrench, Zap, type LucideIcon } from "lucide-react";

import { APP_VERSION } from "@/lib/version";
import { useWorker } from "@/stores/worker";
import { cn } from "@/components/ui/utils";
import { StatusDot, type StatusTone } from "@/components/ui/status";
import type { WorkerState } from "@/api/worker";

export type NavKey = "dashboard" | "automation" | "tools" | "settings";

type NavItem = {
  key: NavKey;
  label: string;
  icon: LucideIcon;
  hint: string;
  /** Visual emphasis — Automation is the core workflow of the app. */
  emphasized?: boolean;
};

/** Workspace navigation (core areas), grouped apart from Settings. */
const WORKSPACE_ITEMS: NavItem[] = [
  {
    key: "dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
    hint: "Workspace at a glance",
  },
  {
    key: "automation",
    label: "Automation",
    icon: Zap,
    hint: "One video → one click",
    emphasized: true,
  },
  { key: "tools", label: "Tools", icon: Wrench, hint: "Single-purpose utilities" },
];

const SETTINGS_ITEMS: NavItem[] = [
  { key: "settings", label: "Settings", icon: Settings, hint: "App configuration" },
];

export function workerStateLabel(state: WorkerState): {
  label: string;
  tone: StatusTone;
} {
  switch (state) {
    case "ready":
      return { label: "Ready", tone: "ok" };
    case "starting":
      return { label: "Starting…", tone: "warn" };
    case "stopping":
      return { label: "Stopping…", tone: "warn" };
    case "stopped":
      return { label: "Stopped", tone: "muted" };
    case "failed":
      return { label: "Failed", tone: "bad" };
  }
}

interface SidebarProps {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
}

export default function Sidebar({ active, onNavigate }: SidebarProps) {
  const { info, hardware } = useWorker();
  const status = workerStateLabel(info?.state ?? "stopped");

  const gpuText = hardware
    ? (hardware.gpu_name ??
      (hardware.gpu_vendor ? hardware.gpu_vendor.toUpperCase() : "CPU (no dedicated GPU)"))
    : "…";
  const ramText = hardware ? `${Math.round(hardware.ram_mb / 1024)} GB` : "…";

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      {/* Brand */}
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-sidebar-border px-4">
        <span className="grid size-7 shrink-0 place-items-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
          AT
        </span>
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold tracking-tight">AutoTranslate</p>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Video Studio</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2" aria-label="Main">
        <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
          Workspace
        </p>
        {WORKSPACE_ITEMS.map(({ key, label, icon: Icon, hint, emphasized }) => {
          const selected = key === active;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key)}
              aria-current={selected ? "page" : undefined}
              title={hint}
              className={cn(
                "relative flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                selected
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                emphasized &&
                  !selected &&
                  "ring-1 ring-inset ring-primary/20 hover:ring-primary/40",
              )}
            >
              {selected && (
                <span
                  className="absolute left-0 h-6 w-0.5 rounded-r bg-primary"
                  aria-hidden="true"
                />
              )}
              <Icon
                className={cn("size-4 shrink-0", emphasized && !selected && "text-primary")}
                aria-hidden="true"
              />
              <span className="truncate">{label}</span>
              {emphasized && (
                <span
                  className={cn(
                    "ml-auto rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
                    selected ? "bg-primary text-primary-foreground" : "bg-primary/15 text-primary",
                  )}
                >
                  Core
                </span>
              )}
            </button>
          );
        })}

        <div className="my-2 border-t border-sidebar-border" />
        <p className="px-3 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
          App
        </p>
        {SETTINGS_ITEMS.map(({ key, label, icon: Icon, hint }) => {
          const selected = key === active;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key)}
              aria-current={selected ? "page" : undefined}
              title={hint}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                selected
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* System footer */}
      <div className="shrink-0 space-y-2 border-t border-sidebar-border p-3 text-xs">
        <div className="flex items-center gap-2">
          <StatusDot tone={status.tone} />
          <span className="font-medium">Worker</span>
          <span
            className={cn(
              "ml-auto",
              status.tone === "bad"
                ? "text-red-400"
                : status.tone === "warn"
                  ? "text-amber-400"
                  : "text-muted-foreground",
            )}
          >
            {status.label}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 text-muted-foreground">
          <span>GPU</span>
          <span className="truncate" title={gpuText}>
            {gpuText}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2 text-muted-foreground">
          <span>Memory</span>
          <span>{ramText}</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-muted-foreground/70">
          <span>Version</span>
          <span>v{APP_VERSION}</span>
        </div>
      </div>
    </aside>
  );
}
