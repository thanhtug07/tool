import type { ArtifactPaths } from "@/api/pipeline";
import type { Project } from "@/api/project";
import type { ProviderView } from "@/api/provider";
import type { CuePatch, SubtitleCue } from "@/api/subtitle";
import type { SubtitleOverlayStyle } from "@/components/subtitleOverlay";
import type { WatermarkConfig } from "@/components/WatermarkConfig";
import type { StageKey, StepConfig } from "@/pages/Automation/automation";
import type {
  ChecklistLineState,
  DerivedStageRun,
  PipelinePhase,
} from "@/pages/Automation/automation";
import type { ToolId } from "@/pages/Tools";
import type { ActiveCustomTool, CustomToolId } from "./customTools";
import type { CustomStep, CustomStepId } from "./customSteps";

/** The two workspace flows — both drive the same pipeline engine. */
export type WorkspaceMode = "automation" | "custom";

/** Custom-workflow controller owned by the workspace (shared engine). */
export type WorkflowController = {
  steps: CustomStep[];
  toggle: (id: CustomStepId) => void;
  move: (id: CustomStepId, dir: -1 | 1) => void;
  add: (id: CustomStepId) => void;
  /** Replace one step's inline config (audio mode / logo region). */
  updateConfig: (id: CustomStepId, config: StepConfig) => void;
  /** Real stages not yet in the workflow (the [+ Add Step] menu). */
  addable: CustomStep[];
  /** Ordered, enabled, runnable stages for `startPipelineWithSteps`. */
  runnable: StageKey[];
};

/** Custom Tool Workspace controller (system-decided stage order). */
export type CustomToolsController = {
  tools: ActiveCustomTool[];
  /** Add or update a configured tool. */
  apply: (tool: ActiveCustomTool) => void;
  remove: (id: CustomToolId) => void;
  reset: () => void;
  /** Dependency-ordered stages the active tools will run. */
  runnable: StageKey[];
};

/** Which pane(s) the center canvas shows. */
export type PreviewMode = "original" | "result" | "split";

/** Real ffprobe metadata shown under the project (never faked). */
export type VideoMeta = {
  duration: number;
  width: number;
  height: number;
  fps: number | null;
  audioTracks: number;
  videoCodec: string | null;
  container: string | null;
};

/** Subtitle/cue state shared by the right panel, timeline and canvas. */
export type CuesController = {
  cues: SubtitleCue[];
  selectedId: string | null;
  select: (id: string | null) => void;
  /** Seek every visible player to `time`. */
  seek: (time: number) => void;
  /** Persist a cue edit through the real backend and refresh locally. */
  update: (id: string, patch: CuePatch) => Promise<void>;
  /** Delete one cue (persisted through the real backend, undoable). */
  remove: (id: string) => Promise<void>;
  /** Undo the last cue edit (restores the pre-edit snapshot via the backend). */
  undo: () => void;
  /** Redo the most recently undone cue edit. */
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
};

/** All pipelined automation options + handlers the panels need. */
export type AutomationOptions = {
  sourceLanguage: string;
  setSourceLanguage: (v: string) => void;
  targetLanguage: string;
  setTargetLanguage: (v: string) => void;
  provider: string;
  setProvider: (v: string) => void;
  providerOptions: ProviderView[];
  selectedProvider: ProviderView | null;
  burnSubtitles: boolean;
  setBurnSubtitles: (v: boolean) => void;
  dubAudio: boolean;
  setDubAudio: (v: boolean) => void;
  voice: string;
  setVoice: (v: string) => void;
  ttsEngine: string;
  setTtsEngine: (v: string) => void;
  watermark: WatermarkConfig;
  setWatermark: (v: WatermarkConfig) => void;
  /** TTS voices from the backend (`settings.voices`), never hard-coded. */
  voiceOptions: { value: string; label: string }[];
  /** Chunked parallel pipeline (TASK_AUTOMATION_PINELINE) — default off. */
  chunked: boolean;
  setChunked: (v: boolean) => void;
  /** Logo removal (delogo) — region in source pixels, wired into the run. */
  logoRemoval: LogoRemovalConfig;
  setLogoRemoval: (v: LogoRemovalConfig) => void;
};

/** Delogo region the user picks on the automation/custom toolbar. */
export type LogoRemovalConfig = {
  enabled: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
};

export const DEFAULT_LOGO_REMOVAL: LogoRemovalConfig = {
  enabled: false,
  x: 0,
  y: 0,
  width: 64,
  height: 64,
};

/** Everything a workspace panel renders against. */
export type WorkspaceContext = {
  project: Project | null;
  busy: boolean;
  phase: PipelinePhase;
  /** Live per-stage runs (real job state). */
  stages: DerivedStageRun[];
  /** Expanded pipeline checklist (extract-audio + sub-stages). */
  checklist: ChecklistLineState[];
  overallProgress: number;
  startedAt: number | null;
  meta: VideoMeta | null;
  artifacts: ArtifactPaths | null;
  /** Loadable URL of the source video (asset protocol in Tauri). */
  videoUrl: string;
  /** Loadable URL of the rendered output (null until render succeeds). */
  resultUrl: string | null;
  options: AutomationOptions;
  /** Live preview subtitle style (editable in Subtitle context). */
  overlay: SubtitleOverlayStyle;
  setOverlay: (style: SubtitleOverlayStyle) => void;
  actions: {
    pickVideo(): void;
    automate(): void;
    cancel(): void;
    retry(): void;
    reprocess(): void;
    export(): void;
    copyPath(): void;
    openOutputFolder(): void;
    openProviderSettings(): void;
    openTool(tool: ToolId, projectId?: string): void;
  };
  /** Custom-workflow builder state (meaningful in `custom` mode only). */
  workflow: WorkflowController;
  /** Custom Tool Workspace state (the new Custom page). */
  customTools: CustomToolsController;
  /**
   * Logo region being dragged on the large preview while the Xóa logo config
   * panel is open. The panel (right) and the canvas overlay (left) share this
   * single source of truth — a drag on the video updates the number inputs
   * live, and vice versa. Null when no logo tool is being configured.
   */
  logoRegion: {
    region: { x: number; y: number; width: number; height: number } | null;
    setRegion: (region: { x: number; y: number; width: number; height: number } | null) => void;
  };
  cues: CuesController;
};
