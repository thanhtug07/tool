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

/** Real translation providers (worker `build_translation_provider` registry). */
export const PROVIDERS = [
  { id: "mock", label: "Mock (offline)", needsKey: false, hint: "Deterministic, no network" },
  { id: "gemini", label: "Gemini (cloud)", needsKey: true, hint: "Best quality, needs API key" },
  {
    id: "local",
    label: "Local LLM",
    needsKey: false,
    hint: "llama.cpp / OpenAI-compatible server",
  },
] as const;

export type ProviderId = (typeof PROVIDERS)[number]["id"];

/** Ordered pipeline stages (job types the backend actually runs). */
export const PIPELINE_STAGE_ORDER = ["transcribe", "translate", "subtitle", "render"] as const;
export type StageKey = (typeof PIPELINE_STAGE_ORDER)[number];

export const STAGE_CHECKLIST = [
  { key: "transcribe", label: "Extract audio & transcribe" },
  { key: "translate", label: "Translate" },
  { key: "subtitle", label: "Generate subtitles" },
  { key: "render", label: "Render video" },
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
  { key: "render", label: "Render video" },
];

/** Stages the current build cannot run — shown honestly as "later". */
export const FUTURE_STAGES = [
  { key: "voice", label: "Voice generation" },
  { key: "mix", label: "Audio mixing" },
  { key: "logo", label: "Logo removal" },
] as const;

// Stage slices over the 0..1 pipeline progress (real job.progress per stage).
const STAGE_SLICES: Array<[number, number]> = [
  [0, 0.25],
  [0.25, 0.5],
  [0.5, 0.75],
  [0.75, 1],
];

/** Options captured when AUTOMATE is clicked. */
export type AutomationOptions = {
  videoPath: string;
  sourceLanguage: string;
  targetLanguage: string;
  provider: string;
  burnSubtitles: boolean;
  watermark: WatermarkConfig;
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
      return { provider: options.provider, target_language: options.targetLanguage };
    case "subtitle":
      return {};
    case "render":
      return {
        ...(options.burnSubtitles ? {} : { burn_subtitles: "false" }),
        ...(watermarkToWire(options.watermark)
          ? { watermark: watermarkToWire(options.watermark) }
          : {}),
      };
  }
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
};

/** Which stage job has been submitted (jobIds are assigned as we go). */
export type PipelinePlan = {
  stages: { key: StageKey; jobId: string | null }[];
  startedAt: number | null;
  options?: PlanOptions;
};

export function initialPipelinePlan(): PipelinePlan {
  return {
    stages: PIPELINE_STAGE_ORDER.map((key) => ({ key, jobId: null })),
    startedAt: null,
  };
}

/** Begin a run, capturing the options the user clicked AUTOMATE with. */
export function startPipeline(options: PlanOptions): PipelinePlan {
  return { ...initialPipelinePlan(), options };
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
  let total = 0;
  for (let i = 0; i < stages.length; i++) {
    const [start, end] = STAGE_SLICES[i];
    const stage = stages[i];
    if (stage.status === "succeeded") {
      total += end - start;
    } else if (stage.status === "running" || stage.status === "queued") {
      total += start + Math.min(1, Math.max(0, stage.progress)) * (end - start);
    }
  }
  return total;
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
