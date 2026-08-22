import type { SubtitleOverlayStyle } from "@/components/subtitleOverlay";
import type { WatermarkConfig } from "@/components/WatermarkConfig";

/** Real source-language options (worker accepts any ISO-2 code; auto = detect). */
export const SOURCE_LANGUAGES = [
  { code: "", label: "Auto Detect" },
  { code: "zh", label: "Chinese" },
  { code: "en", label: "English" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "vi", label: "Vietnamese" },
] as const;

/** Real target-language options (translation provider translates to any code). */
export const TARGET_LANGUAGES = [
  { code: "vi", label: "Vietnamese" },
  { code: "en", label: "English" },
  { code: "zh", label: "Chinese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
] as const;

export function languageLabel(code: string): string {
  const all = [...SOURCE_LANGUAGES, ...TARGET_LANGUAGES];
  return all.find((l) => l.code === code)?.label ?? code;
}

/** Ordered pipeline stages (job types the backend actually runs). */
export const PIPELINE_STAGE_ORDER = [
  "transcribe",
  "translate",
  "subtitle",
  "tts",
  "render",
] as const;
/** Extra stages that only exist in the custom workflow (not the one-click run). */
export const EXTRA_CUSTOM_STAGES = ["audio", "logo"] as const;
/** The chunked pipeline stage — replaces the whole STT→translate→TTS→subtitle
 * chain with one job that processes the video in fixed-length chunks under
 * bounded concurrency (TASK_AUTOMATION_PINELINE), then renders + validates. */
export const CHUNK_STAGE = "chunk" as const;
export type StageKey =
  (typeof PIPELINE_STAGE_ORDER)[number] | (typeof EXTRA_CUSTOM_STAGES)[number] | typeof CHUNK_STAGE;

export const STAGE_CHECKLIST = [
  { key: "transcribe", label: "Extract audio & transcribe" },
  { key: "translate", label: "Translate" },
  { key: "subtitle", label: "Generate subtitles" },
  { key: "tts", label: "Generate voice" },
  { key: "render", label: "Render video" },
  { key: "chunk", label: "Chunked pipeline" },
] as const;

/**
 * Expanded pipeline checklist shown while processing: real worker sub-stages
 * (pipeline_runner.rs reports `extract-audio` then `transcribe` for the
 * transcribe job) plus the future stages that are NOT in this build (they are
 * always rendered as "later", never as pending work).
 */
export const PIPELINE_CHECKLIST: {
  key: StageKey;
  label: string;
  subStage?: string;
}[] = [
  { key: "transcribe", label: "Extract audio", subStage: "extract-audio" },
  { key: "transcribe", label: "Speech-to-text", subStage: "transcribe" },
  { key: "translate", label: "Translate" },
  { key: "subtitle", label: "Generate subtitles" },
  { key: "tts", label: "Generate voice" },
  { key: "render", label: "Render video" },
  {
    key: "chunk",
    label: "Chunked pipeline (30s parallel chunks)",
    subStage: "chunk",
  },
];

/** Stages the current build cannot run — shown honestly as "later". Empty: all
 * pipeline stages (incl. audio separation + logo removal) are real backend
 * stages now. */
export const FUTURE_STAGES = [] as const;

/** Per-custom-step configuration (audio mode / logo region / provider). */
export type StepConfig = {
  mode?: "vocal_removal" | "normalize" | "denoise";
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  timeStart?: number;
  timeEnd?: number;
  /**
   * Tool-specific translation provider — lets two applied tools (e.g. Dịch
   * video + Lồng tiếng) keep their own providers instead of the last-applied
   * tool silently overriding the shared workspace provider.
   */
  provider?: string;
};

/** Options captured when AUTOMATE is clicked. */
export type AutomationOptions = {
  videoPath: string;
  sourceLanguage: string;
  targetLanguage: string;
  provider: string;
  burnSubtitles: boolean;
  dubAudio: boolean;
  voice: string;
  ttsEngine: string;
  watermark: WatermarkConfig;
  /**
   * Caption position override (dragged spot in the preview) for the
   * burned-in subtitles; sent to the render stage as `subtitle_style`.
   */
  subtitleStyle?: SubtitleOverlayStyle;
  /** Config of the custom-workflow step being submitted (audio/logo). */
  stepConfig?: StepConfig;
  /**
   * Stages included in the current run — the render stage uses it to pick up
   * the custom-workflow artifacts (logo removal / processed audio).
   */
  enabledStages?: StageKey[];
};

/** Build the `job.submit` params for a stage (pure — unit tested). */
export function buildStageParams(
  stage: StageKey,
  options: AutomationOptions,
): Record<string, unknown> {
  switch (stage) {
    case "transcribe":
      return {
        video_path: options.videoPath,
        ...(options.sourceLanguage ? { language: options.sourceLanguage } : {}),
      };
    case "translate":
      return {
        // A custom tool may pin its own provider; the one-click run uses the
        // workspace provider.
        provider: options.stepConfig?.provider ?? options.provider,
        target_language: options.targetLanguage,
      };
    case "subtitle":
      return {};
    case "tts":
      return {
        target_language: options.targetLanguage,
        ...(options.ttsEngine ? { engine: options.ttsEngine } : {}),
        ...(options.voice ? { voice: options.voice } : {}),
      };
    case "audio":
      return { audio_mode: options.stepConfig?.mode ?? "vocal_removal" };
    case "logo":
      return {
        logo_x: options.stepConfig?.x ?? 0,
        logo_y: options.stepConfig?.y ?? 0,
        logo_width: options.stepConfig?.width ?? 64,
        logo_height: options.stepConfig?.height ?? 64,
        ...(options.stepConfig?.timeStart !== undefined
          ? { logo_time_start: options.stepConfig.timeStart }
          : {}),
        ...(options.stepConfig?.timeEnd !== undefined
          ? { logo_time_end: options.stepConfig.timeEnd }
          : {}),
      };
    case "render":
      return {
        ...(options.burnSubtitles ? {} : { burn_subtitles: "false" }),
        ...(options.dubAudio ? { voice_track: "true" } : {}),
        ...(watermarkToWire(options.watermark)
          ? { watermark: watermarkToWire(options.watermark) }
          : {}),
        ...(options.burnSubtitles && options.subtitleStyle
          ? { subtitle_style: subtitleStyleToWire(options.subtitleStyle) }
          : {}),
        // The custom workflow's pre-processing steps write artifacts the
        // render picks up (logo-free video / processed audio track).
        ...(options.enabledStages?.includes("logo") ? { logo_removed: "true" } : {}),
        ...(options.enabledStages?.includes("audio") ? { audio_mix: "true" } : {}),
      };
    case "chunk":
      // One job runs the whole chain chunked (STT → translate → TTS →
      // subtitle → render → final validation). It carries every option the
      // run needs so the single job is self-contained.
      return {
        provider: options.stepConfig?.provider ?? options.provider,
        target_language: options.targetLanguage,
        ...(options.sourceLanguage ? { source_language: options.sourceLanguage } : {}),
        ...(options.dubAudio ? { dub: "true" } : {}),
        ...(options.dubAudio && options.ttsEngine ? { engine: options.ttsEngine } : {}),
        ...(options.dubAudio && options.voice ? { voice: options.voice } : {}),
        ...(options.burnSubtitles ? {} : { burn_subtitles: "false" }),
        ...(watermarkToWire(options.watermark)
          ? { watermark: watermarkToWire(options.watermark) }
          : {}),
        ...(options.burnSubtitles && options.subtitleStyle
          ? { subtitle_style: subtitleStyleToWire(options.subtitleStyle) }
          : {}),
      };
  }
}

/**
 * Map the preview's caption style to the render `subtitle_style` wire format:
 * the position preset plus the dragged custom anchor (frame fractions). The
 * worker rebuilds the burn-in ASS with this placement.
 */
export function subtitleStyleToWire(style: SubtitleOverlayStyle): Record<string, unknown> {
  return {
    position: style.position,
    ...(style.customX !== undefined ? { custom_x: style.customX } : {}),
    ...(style.customY !== undefined ? { custom_y: style.customY } : {}),
  };
}

/**
 * Map the UI watermark model to the worker's `/v1/render` wire format
 * (`{"text": {...}}` or `{"image": {...}}`). Returns null when disabled.
 */
export function watermarkToWire(config: WatermarkConfig): Record<string, unknown> | null {
  if (config.kind === "none") return null;
  if (config.kind === "text") {
    return {
      text: {
        text: config.text ?? "",
        position: config.position,
        margin: config.margin,
        x: config.x,
        y: config.y,
        font_size: config.fontSize,
        color: config.color,
        opacity: config.opacity,
        rotation: config.rotation,
        ...(config.font ? { font: config.font } : {}),
      },
    };
  }
  return {
    image: {
      image_path: config.imagePath ?? "",
      position: config.position,
      margin: config.margin,
      x: config.x,
      y: config.y,
      width: config.imageWidth,
      opacity: config.opacity,
    },
  };
}

// ---- pipeline orchestration (pure) ---------------------------------------

export type StageStatus = "pending" | "queued" | "running" | "succeeded" | "failed" | "cancelled";

/** The options a run was started with (kept so the result view can report them). */
export type PlanOptions = {
  sourceLanguage: string;
  targetLanguage: string;
  provider: string;
  dubAudio: boolean;
};

/** Which stage job has been submitted (jobIds are assigned as we go). */
export type PipelinePlan = {
  stages: { key: StageKey; jobId: string | null }[];
  startedAt: number | null;
  options?: PlanOptions;
};

export function initialPipelinePlan(dubAudio = false): PipelinePlan {
  return {
    // The tts stage only exists when dubbing is enabled; otherwise it is
    // skipped entirely (the stage-submission loop advances past it).
    stages: PIPELINE_STAGE_ORDER.filter((key) => key !== "tts" || dubAudio).map((key) => ({
      key,
      jobId: null,
    })),
    startedAt: null,
  };
}

/** Begin a run, capturing the options the user clicked AUTOMATE with. */
export function startPipeline(options: PlanOptions): PipelinePlan {
  return { ...initialPipelinePlan(options.dubAudio), options };
}

/**
 * Ordered stages for the one-click Automation run: the fixed pipeline plus
 * any optional pre-render stages (logo removal) wired from the toolbar. The
 * ``logo`` stage runs delogo and the render picks up ``logo_removed.mp4``.
 *
 * With ``chunked`` the whole STT→translate→TTS→subtitle→render chain collapses
 * into the single ``chunk`` job (TASK_AUTOMATION_PINELINE) — the backend runs
 * the same provider/voice options in parallel 30 s chunks.
 */
export function automationPipelineSteps(
  dubAudio: boolean,
  logoRemoval: boolean,
  chunked = false,
): StageKey[] {
  if (chunked) return ["chunk"];
  const steps: StageKey[] = ["transcribe", "translate", "subtitle"];
  if (dubAudio) steps.push("tts");
  if (logoRemoval) steps.push("logo");
  steps.push("render");
  return steps;
}

/**
 * Begin a run from an explicit ordered list of stages (the Custom workflow).
 * Only the given stages are scheduled — the orchestration loop advances
 * through exactly this order, so the same engine runs both flows.
 */
export function startPipelineWithSteps(
  steps: readonly StageKey[],
  options: PlanOptions,
): PipelinePlan {
  return {
    stages: steps.map((key) => ({ key, jobId: null })),
    startedAt: null,
    options,
  };
}

/** Record the jobId returned by `job.submit` for a stage. */
export function markStageSubmitted(plan: PipelinePlan, key: StageKey, jobId: string): PipelinePlan {
  return {
    ...plan,
    startedAt: plan.startedAt ?? Date.now(),
    stages: plan.stages.map((s) => (s.key === key ? { ...s, jobId } : s)),
  };
}

export type DerivedStageRun = {
  key: StageKey;
  jobId: string | null;
  status: StageStatus;
  progress: number;
  stage: string;
  errorCode: string | null;
  errorMessage: string | null;
};

/**
 * Derive the live status of each stage from the shared job snapshot — the
 * Automation page reads the same jobs store as the Dashboard, so both always
 * reflect the same job state.
 */
export function deriveStages(
  plan: PipelinePlan,
  jobs: {
    id: string;
    status: string;
    progress: number;
    stage: string;
    error_code: string | null;
    error_message: string | null;
  }[],
): DerivedStageRun[] {
  return plan.stages.map((stage) => {
    const job = stage.jobId ? jobs.find((j) => j.id === stage.jobId) : undefined;
    if (!job) {
      return {
        key: stage.key,
        jobId: stage.jobId,
        status: "pending",
        progress: 0,
        stage: "",
        errorCode: null,
        errorMessage: null,
      };
    }
    return {
      key: stage.key,
      jobId: stage.jobId,
      status: job.status as StageStatus,
      progress: job.progress,
      stage: job.stage,
      errorCode: job.error_code,
      errorMessage: job.error_message,
    };
  });
}

export type PipelinePhase = "idle" | "running" | "succeeded" | "failed" | "cancelled";

/**
 * Coarse phase derived from the stage runs + plan start marker. The pipeline
 * stays `running` from the moment it starts until every stage succeeded, so
 * the brief gap between a stage completing and the next being submitted never
 * flickers the UI back to idle.
 */
export function derivePhase(stages: DerivedStageRun[], startedAt: number | null): PipelinePhase {
  if (stages.some((s) => s.status === "failed")) return "failed";
  if (stages.some((s) => s.status === "cancelled")) return "cancelled";
  if (stages.every((s) => s.status === "succeeded")) return "succeeded";
  if (startedAt !== null) return "running";
  return "idle";
}

/**
 * Overall pipeline progress from the real per-stage `job.progress` values.
 * Each stage owns a 25% slice; completed stages count fully, the active stage
 * counts its own progress within its slice.
 */
export function pipelineProgress(stages: DerivedStageRun[]): number {
  // Each stage owns an equal slice, so the math is correct whether the run
  // has 4 stages (no dubbing) or 5 (dubbing adds the tts stage).
  const n = stages.length;
  if (n === 0) return 0;
  let total = 0;
  for (let i = 0; i < n; i++) {
    const start = i / n;
    const end = (i + 1) / n;
    const stage = stages[i];
    if (stage.status === "succeeded") {
      total += end - start;
    } else if (stage.status === "running" || stage.status === "queued") {
      total += start + Math.min(1, Math.max(0, stage.progress)) * (end - start);
    }
  }
  return total;
}

/** Weighted pipeline progress from tasks (v2 orchestrator): average task progress. */
export function pipelineProgressFromTasks(tasks: { status: string; progress: number }[]): number {
  if (tasks.length === 0) return 0;
  const total = tasks.reduce((sum, t) => {
    if (t.status === "succeeded") return sum + 1;
    if (t.status === "running" || t.status === "ready" || t.status === "queued") {
      return sum + Math.min(1, Math.max(0, t.progress));
    }
    return sum;
  }, 0);
  return total / tasks.length;
}

export type ChecklistLineState = {
  key: StageKey;
  label: string;
  subStage?: string;
  status: StageStatus;
};

/**
 * Per-line state for `PIPELINE_CHECKLIST` derived from the real stage runs.
 * The `extract-audio` line reads the transcribe job's live `stage` string, so
 * it flips to done exactly when the worker moves past extraction.
 */
export function checklistState(stages: DerivedStageRun[]): ChecklistLineState[] {
  return PIPELINE_CHECKLIST.map((line) => {
    const run = stages.find((s) => s.key === line.key);
    const status = run?.status ?? "pending";
    if (
      line.subStage === "extract-audio" &&
      status === "running" &&
      run?.stage !== "extract-audio"
    ) {
      return { ...line, status: "succeeded" };
    }
    if (line.subStage && status === "running" && run?.stage !== line.subStage) {
      return { ...line, status: "pending" };
    }
    return { ...line, status };
  });
}

/** Human-readable label for the current running stage. */
export function currentStageLabel(stages: DerivedStageRun[]): string {
  const active = stages.find((s) => s.status === "running" || s.status === "queued");
  if (!active) return "Preparing…";
  return STAGE_CHECKLIST.find((s) => s.key === active.key)?.label ?? active.key;
}
