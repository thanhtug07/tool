import type { SubtitleOverlayStyle } from "@/components/subtitleOverlay";
import type { WatermarkConfig } from "@/components/WatermarkConfig";
import type { PipelinePlan } from "@/pages/Automation/automation";
import type { ActiveCustomTool, CustomToolConfig } from "./customTools";
import type { LogoRemovalConfig } from "./types";

/**
 * Persisted per-project automation options (the workspace's real UI state).
 * Kept in localStorage so a navigation away and back — or a full reload —
 * restores exactly what the user configured (provider, dubbing, voice, …)
 * instead of resetting to defaults and silently changing the next run.
 */
export type StudioSessionOptions = {
  sourceLanguage: string;
  targetLanguage: string;
  provider: string;
  burnSubtitles: boolean;
  dubAudio: boolean;
  voice: string;
  ttsEngine: string;
  watermark: WatermarkConfig;
  /** Caption overlay style (position preset + dragged custom spot). */
  overlay?: SubtitleOverlayStyle;
  /** Delogo region (automation toolbar). */
  logoRemoval?: LogoRemovalConfig;
  /** Chunked parallel pipeline (TASK_AUTOMATION_PINELINE) — default off. */
  chunked?: boolean;
  /** Directory the rendered video is auto-exported into on success ('' = none). */
  outputFolder?: string;
};

const planKey = (projectId: string) => `studio.plan.${projectId}`;
const optionsKey = (projectId: string) => `studio.options.${projectId}`;
const customToolsKey = (projectId: string) => `studio.customTools.${projectId}`;

function read<T>(key: string, guard: (value: unknown) => value is T): T | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return guard(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPlan(value: unknown): value is PipelinePlan {
  if (!isRecord(value)) return false;
  return (
    Array.isArray(value.stages) &&
    value.stages.every(
      (s) =>
        isRecord(s) &&
        typeof s.key === "string" &&
        (s.jobId === null || typeof s.jobId === "string"),
    ) &&
    (typeof value.startedAt === "number" || value.startedAt === null)
  );
}

function isOverlay(value: unknown): value is SubtitleOverlayStyle {
  if (!isRecord(value)) return false;
  return (
    typeof value.font === "string" &&
    typeof value.fontSizePlayRes === "number" &&
    typeof value.strokePlayRes === "number" &&
    typeof value.shadowPlayRes === "number" &&
    typeof value.position === "string" &&
    typeof value.bgBox === "boolean" &&
    (value.customX === undefined || typeof value.customX === "number") &&
    (value.customY === undefined || typeof value.customY === "number")
  );
}

function isLogoRemoval(value: unknown): value is LogoRemovalConfig {
  return (
    isRecord(value) &&
    typeof value.enabled === "boolean" &&
    ["x", "y", "width", "height"].every((k) => typeof value[k] === "number")
  );
}

function isCustomToolConfig(value: unknown): value is CustomToolConfig {
  if (!isRecord(value)) return false;
  // Accept any subset of known keys; numbers/strings/booleans are validated.
  const numKeys = ["logo.x", "logo.y", "logo.width", "logo.height"] as const;
  for (const key of numKeys) {
    const [obj, k] = key.split(".") as [string, string];
    if (value[obj] !== undefined) {
      const inner = value[obj];
      if (!isRecord(inner) || typeof inner[k] !== "number") return false;
    }
  }
  const strKeys = [
    "audioMode",
    "sourceLanguage",
    "dubSourceLanguage",
    "dubTargetLanguage",
    "dubProvider",
    "dubVoice",
    "translateSourceLanguage",
    "translateTargetLanguage",
    "translateProvider",
  ] as const;
  for (const key of strKeys) {
    if (value[key] !== undefined && typeof value[key] !== "string") return false;
  }
  const boolKeys = [
    "keepBackgroundMusic",
    "generateSubtitles",
    "burnSubtitles",
    "generateDub",
  ] as const;
  for (const key of boolKeys) {
    if (value[key] !== undefined && typeof value[key] !== "boolean") return false;
  }
  return true;
}

function isActiveCustomTool(value: unknown): value is ActiveCustomTool {
  if (!isRecord(value) || typeof value.id !== "string") return false;
  return value.config === undefined || isCustomToolConfig(value.config);
}

function isCustomTools(value: unknown): value is ActiveCustomTool[] {
  return Array.isArray(value) && value.every(isActiveCustomTool);
}

function isOptions(value: unknown): value is StudioSessionOptions {
  if (!isRecord(value)) return false;
  return (
    typeof value.sourceLanguage === "string" &&
    typeof value.targetLanguage === "string" &&
    typeof value.provider === "string" &&
    typeof value.burnSubtitles === "boolean" &&
    typeof value.dubAudio === "boolean" &&
    typeof value.voice === "string" &&
    typeof value.ttsEngine === "string" &&
    isRecord(value.watermark) &&
    (value.overlay === undefined || isOverlay(value.overlay)) &&
    (value.logoRemoval === undefined || isLogoRemoval(value.logoRemoval)) &&
    (value.chunked === undefined || typeof value.chunked === "boolean") &&
    (value.outputFolder === undefined || typeof value.outputFolder === "string")
  );
}

/** Restore the last pipeline plan for a project (job ids included). */
export function loadStudioPlan(projectId: string): PipelinePlan | null {
  return read(planKey(projectId), isPlan);
}

/** Persist the current pipeline plan for a project. */
export function saveStudioPlan(projectId: string, plan: PipelinePlan): void {
  try {
    localStorage.setItem(planKey(projectId), JSON.stringify(plan));
  } catch {
    // Outside the browser shell persistence is unavailable — best effort.
  }
}

/** Restore the last automation options for a project. */
export function loadStudioOptions(projectId: string): StudioSessionOptions | null {
  return read(optionsKey(projectId), isOptions);
}

/** Persist the current automation options for a project. */
export function saveStudioOptions(projectId: string, options: StudioSessionOptions): void {
  try {
    localStorage.setItem(optionsKey(projectId), JSON.stringify(options));
  } catch {
    // Best effort.
  }
}

/** Restore the active Custom-tool list for a project. */
export function loadStudioCustomTools(projectId: string): ActiveCustomTool[] | null {
  return read(customToolsKey(projectId), isCustomTools);
}

/** Persist the active Custom-tool list for a project. */
export function saveStudioCustomTools(projectId: string, tools: ActiveCustomTool[]): void {
  try {
    localStorage.setItem(customToolsKey(projectId), JSON.stringify(tools));
  } catch {
    // Best effort.
  }
}
