import type { LucideIcon } from "lucide-react";
import {
  AudioLines,
  Captions,
  Clapperboard,
  Languages,
  Layers,
  Mic,
  Stamp,
  Volume2,
} from "lucide-react";

import type { SubtitleOverlayStyle } from "@/components/subtitleOverlay";
import type { StageKey, StepConfig } from "@/pages/Automation/automation";
import type { CustomStep } from "./customSteps";

/**
 * Custom Tool Workspace — the Custom page is a tool workspace, not a pipeline
 * editor. The user picks a tool, configures it in a panel, applies it, and
 * presses Run. Every tool maps to REAL backend stages (the same pipeline
 * engine as Automation); the system resolves the execution order from stage
 * dependencies — users never reorder anything.
 */

export type CustomToolId =
  | "audio-separate"
  | "subtitle-generate"
  | "dub"
  | "translate-video"
  | "burn-subtitles"
  | "logo-remove";

/** Audio separation / mix mode (worker `/v1/audio/process`). */
export type AudioMode = "vocal_removal" | "normalize" | "denoise";

/** Per-tool configuration persisted with the workspace session. */
export type CustomToolConfig = {
  // audio-separate
  audioMode?: AudioMode;
  // subtitle-generate
  sourceLanguage?: string;
  // dub
  dubSourceLanguage?: string;
  dubTargetLanguage?: string;
  dubProvider?: string;
  dubVoice?: string;
  dubEngine?: string;
  keepBackgroundMusic?: boolean;
  // translate-video (shares the dub voice keys above for its dub option)
  translateSourceLanguage?: string;
  translateTargetLanguage?: string;
  translateProvider?: string;
  generateSubtitles?: boolean;
  burnSubtitles?: boolean;
  generateDub?: boolean;
  // burn-subtitles — caption overlay style
  overlay?: SubtitleOverlayStyle;
  // logo-remove — region in source pixels
  logo?: { x: number; y: number; width: number; height: number };
};

export type ActiveCustomTool = { id: CustomToolId; config: CustomToolConfig };

export type CustomToolDef = {
  id: CustomToolId;
  name: string;
  description: string;
  icon: LucideIcon;
  /** Real backend stages this tool runs, in dependency order. */
  stages: StageKey[];
};

/** Global dependency order — the system decides, users never reorder. */
export const CUSTOM_STAGE_ORDER: StageKey[] = [
  "transcribe",
  "translate",
  "subtitle",
  "tts",
  "audio",
  "logo",
  "render",
];

export const CUSTOM_TOOLS: CustomToolDef[] = [
  {
    id: "audio-separate",
    name: "Tách âm thanh",
    description: "Voice / Music",
    icon: AudioLines,
    stages: ["audio"],
  },
  {
    id: "subtitle-generate",
    name: "Tạo phụ đề",
    description: "STT → Subtitle",
    icon: Captions,
    stages: ["transcribe", "translate", "subtitle"],
  },
  {
    id: "dub",
    name: "Lồng tiếng",
    description: "Voice → Video",
    icon: Mic,
    stages: ["transcribe", "translate", "subtitle", "tts", "render"],
  },
  {
    id: "translate-video",
    name: "Dịch video",
    description: "Translate + Subtitle",
    icon: Languages,
    stages: ["transcribe", "translate", "subtitle", "render"],
  },
  {
    id: "burn-subtitles",
    name: "Chèn phụ đề",
    description: "Subtitle → Video",
    icon: Captions,
    stages: ["subtitle", "render"],
  },
  {
    id: "logo-remove",
    name: "Xóa logo",
    description: "Remove watermark",
    icon: Stamp,
    stages: ["logo", "render"],
  },
];

export function toolDef(id: CustomToolId): CustomToolDef {
  const def = CUSTOM_TOOLS.find((t) => t.id === id);
  if (!def) throw new Error(`unknown custom tool: ${id}`);
  return def;
}

/**
 * Merge the active tools' stages into a dependency-ordered, deduplicated
 * stage list (system-decided — users never reorder).
 */
export function stagesForTools(tools: ActiveCustomTool[]): StageKey[] {
  const wanted = new Set<StageKey>();
  for (const tool of tools) {
    for (const stage of toolDef(tool.id).stages) wanted.add(stage);
  }
  return CUSTOM_STAGE_ORDER.filter((s) => wanted.has(s));
}

/**
 * Build the ordered CustomStep[] the pipeline engine consumes from the active
 * tools — each step carries its real inline config (audio mode / logo region).
 */
export function buildStepsFromTools(tools: ActiveCustomTool[]): CustomStep[] {
  const stages = stagesForTools(tools);
  return stages.map((key) => {
    const owner = tools.find((t) => toolDef(t.id).stages.includes(key));
    let config: StepConfig | undefined;
    if (owner) {
      if (key === "audio" && owner.config.audioMode) {
        config = { mode: owner.config.audioMode };
      } else if (key === "logo" && owner.config.logo) {
        config = { ...owner.config.logo };
      } else if (key === "translate" && owner.config.translateProvider) {
        // Each tool keeps its own translation provider — with several tools
        // applied, the translate stage must not silently use the last one's.
        config = { provider: owner.config.translateProvider };
      }
    }
    return { id: key, label: stageLabel(key), enabled: true, config };
  });
}

function stageLabel(key: StageKey): string {
  return stageMeta(key).label;
}

/** Display metadata (label + icon) for every pipeline stage the tools run. */
export function stageMeta(key: StageKey): { label: string; icon: LucideIcon } {
  const meta: Record<StageKey, { label: string; icon: LucideIcon }> = {
    transcribe: { label: "Speech-to-Text", icon: Mic },
    translate: { label: "Translation", icon: Languages },
    subtitle: { label: "Subtitles", icon: Captions },
    tts: { label: "Voice", icon: Volume2 },
    audio: { label: "Audio", icon: AudioLines },
    logo: { label: "Logo removal", icon: Stamp },
    render: { label: "Render", icon: Clapperboard },
    chunk: { label: "Chunked", icon: Layers },
  };
  return meta[key];
}

/** Whether two tools share at least one backend stage (used by Remove). */
export function toolsShareStage(a: ActiveCustomTool, b: ActiveCustomTool): boolean {
  const aStages = new Set(toolDef(a.id).stages);
  return toolDef(b.id).stages.some((s) => aStages.has(s));
}
