import type { WorkerState } from "@/api/worker";
import type { StatusTone } from "@/components/ui/status";

/**
 * Top-level areas of the desktop shell. Home is the project hub; Automation
 * and Custom both drive the SAME pipeline engine from the shared workspace —
 * Automation runs the fixed one-click flow, Custom lets the user pick which
 * steps run (the two only differ in how the workflow is controlled, never in
 * the engine). `tools` is an internal route (opened from the workspace, e.g.
 * the Subtitle Editor) and is intentionally not a top-level tab.
 */
export type NavKey = "home" | "automation" | "custom" | "tools" | "settings";

/** Primary top-bar areas (rendered as tabs; Settings stays reachable). */
export const NAV_AREAS: { key: NavKey; label: string }[] = [
  { key: "home", label: "Home" },
  { key: "automation", label: "Automation" },
  { key: "custom", label: "Custom" },
  { key: "settings", label: "Settings" },
];

/** Human label + tone for a worker state (shared by TopBar + Home). */
export function workerStateLabel(state: WorkerState): { label: string; tone: StatusTone } {
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
