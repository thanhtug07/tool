import { describe, expect, it } from "vitest";

import { DEFAULT_WATERMARK, type WatermarkConfig } from "@/components/WatermarkConfig";
import {
  automationPipelineSteps,
  buildStageParams,
  derivePhase,
  deriveStages,
  initialPipelinePlan,
  languageLabel,
  markStageSubmitted,
  pipelineProgress,
  startPipeline,
  startPipelineWithSteps,
  subtitleStyleToWire,
  watermarkToWire,
} from "./automation";

const OPTIONS = {
  videoPath: "C:\\videos\\clip.mp4",
  sourceLanguage: "",
  targetLanguage: "vi",
  provider: "gemini",
  burnSubtitles: true,
  dubAudio: false,
  voice: "vi-VN-HoaiMyNeural",
  ttsEngine: "edge",
  watermark: DEFAULT_WATERMARK,
};

describe("buildStageParams", () => {
  it("transcribe passes the video path and optional source language", () => {
    expect(buildStageParams("transcribe", OPTIONS)).toEqual({ video_path: "C:\\videos\\clip.mp4" });
    expect(buildStageParams("transcribe", { ...OPTIONS, sourceLanguage: "zh" })).toEqual({
      video_path: "C:\\videos\\clip.mp4",
      language: "zh",
    });
  });

  it("translate passes the provider and target language", () => {
    expect(buildStageParams("translate", OPTIONS)).toEqual({
      provider: "gemini",
      target_language: "vi",
    });
  });

  it("translate prefers the step's own provider over the workspace default", () => {
    // Two applied tools may pin different providers — the translate stage must
    // follow the tool that owns it, not the last-applied shared option.
    expect(
      buildStageParams("translate", {
        ...OPTIONS,
        provider: "free",
        stepConfig: { provider: "gemini" },
      }),
    ).toEqual({ provider: "gemini", target_language: "vi" });
  });

  it("render omits burn_subtitles when burning is on (backend default)", () => {
    expect(buildStageParams("render", OPTIONS)).toEqual({});
  });

  it("render disables burn-in explicitly when the user turns it off", () => {
    expect(buildStageParams("render", { ...OPTIONS, burnSubtitles: false })).toEqual({
      burn_subtitles: "false",
    });
  });

  it("render includes the watermark wire payload when configured", () => {
    const watermark: WatermarkConfig = {
      ...DEFAULT_WATERMARK,
      kind: "text",
      text: "Brand",
      position: "top-right",
      fontSize: 64,
      opacity: 0.8,
    };
    const params = buildStageParams("render", { ...OPTIONS, watermark });
    expect(params.watermark).toEqual({
      text: {
        text: "Brand",
        position: "top-right",
        margin: 24,
        x: 0,
        y: 0,
        font_size: 64,
        color: "#FFFFFFFF",
        opacity: 0.8,
        rotation: 0,
      },
    });
  });

  it("render sends the dragged caption position as subtitle_style", () => {
    const subtitleStyle = {
      font: "Arial",
      fontSizePlayRes: 44,
      strokePlayRes: 2,
      shadowPlayRes: 1,
      position: "custom" as const,
      bgBox: false,
      customX: 0.3,
      customY: 0.65,
    };
    const params = buildStageParams("render", { ...OPTIONS, subtitleStyle });
    expect(params.subtitle_style).toEqual({ position: "custom", custom_x: 0.3, custom_y: 0.65 });
  });

  it("render omits subtitle_style when burning is off", () => {
    const subtitleStyle = {
      font: "Arial",
      fontSizePlayRes: 44,
      strokePlayRes: 2,
      shadowPlayRes: 1,
      position: "custom" as const,
      bgBox: false,
      customX: 0.3,
      customY: 0.65,
    };
    const params = buildStageParams("render", {
      ...OPTIONS,
      burnSubtitles: false,
      subtitleStyle,
    });
    expect(params.subtitle_style).toBeUndefined();
  });

  it("audio sends the configured processing mode", () => {
    expect(buildStageParams("audio", OPTIONS)).toEqual({ audio_mode: "vocal_removal" });
    expect(buildStageParams("audio", { ...OPTIONS, stepConfig: { mode: "denoise" } })).toEqual({
      audio_mode: "denoise",
    });
  });

  it("logo sends the marked region (with optional time window)", () => {
    expect(
      buildStageParams("logo", { ...OPTIONS, stepConfig: { x: 10, y: 20, width: 80, height: 40 } }),
    ).toEqual({ logo_x: 10, logo_y: 20, logo_width: 80, logo_height: 40 });
    expect(
      buildStageParams("logo", {
        ...OPTIONS,
        stepConfig: { x: 0, y: 0, width: 64, height: 64, timeStart: 1, timeEnd: 5 },
      }),
    ).toEqual({
      logo_x: 0,
      logo_y: 0,
      logo_width: 64,
      logo_height: 64,
      logo_time_start: 1,
      logo_time_end: 5,
    });
  });

  it("render picks up the custom-workflow artifacts only when their steps ran", () => {
    expect(buildStageParams("render", OPTIONS)).toEqual({});
    expect(buildStageParams("render", { ...OPTIONS, enabledStages: ["logo"] })).toEqual({
      logo_removed: "true",
    });
    expect(buildStageParams("render", { ...OPTIONS, enabledStages: ["audio", "logo"] })).toEqual({
      logo_removed: "true",
      audio_mix: "true",
    });
  });
});

describe("subtitleStyleToWire", () => {
  it("maps a custom preset to the worker's wire fields", () => {
    expect(
      subtitleStyleToWire({
        font: "Arial",
        fontSizePlayRes: 44,
        strokePlayRes: 2,
        shadowPlayRes: 1,
        position: "custom",
        bgBox: false,
        customX: 0.4,
        customY: 0.8,
      }),
    ).toEqual({ position: "custom", custom_x: 0.4, custom_y: 0.8 });
    expect(
      subtitleStyleToWire({
        font: "Arial",
        fontSizePlayRes: 44,
        strokePlayRes: 2,
        shadowPlayRes: 1,
        position: "bottom_center",
        bgBox: false,
      }),
    ).toEqual({ position: "bottom_center" });
  });
});

describe("watermarkToWire", () => {
  it("returns null when disabled", () => {
    expect(watermarkToWire(DEFAULT_WATERMARK)).toBeNull();
  });

  it("maps text watermarks to the worker /v1/render shape", () => {
    const wire = watermarkToWire({ ...DEFAULT_WATERMARK, kind: "text", text: "Studio" });
    expect(wire).toEqual({
      text: {
        text: "Studio",
        position: "bottom-right",
        margin: 24,
        x: 0,
        y: 0,
        font_size: 48,
        color: "#FFFFFFFF",
        opacity: 1,
        rotation: 0,
      },
    });
  });

  it("maps image watermarks with width + opacity", () => {
    const wire = watermarkToWire({
      ...DEFAULT_WATERMARK,
      kind: "image",
      imagePath: "C:\\logo.png",
      imageWidth: 200,
      opacity: 0.5,
    });
    expect(wire).toEqual({
      image: {
        image_path: "C:\\logo.png",
        position: "bottom-right",
        margin: 24,
        x: 0,
        y: 0,
        width: 200,
        opacity: 0.5,
      },
    });
  });
});

describe("chunked pipeline (TASK_AUTOMATION_PINELINE)", () => {
  it("chunk stage params carry provider, language, dub + render options", () => {
    const params = buildStageParams("chunk", {
      ...OPTIONS,
      sourceLanguage: "zh",
      dubAudio: true,
    });
    expect(params.provider).toBe("gemini");
    expect(params.target_language).toBe("vi");
    expect(params.source_language).toBe("zh");
    expect(params.dub).toBe("true");
    expect(params.engine).toBe("edge");
    expect(params.voice).toBe("vi-VN-HoaiMyNeural");
    expect(params.burn_subtitles).toBeUndefined();
    // The default watermark kind is "none" → no watermark wire payload.
    expect(params.watermark).toBeUndefined();
  });

  it("chunk stage omits dub options when dubbing is off", () => {
    const params = buildStageParams("chunk", { ...OPTIONS, dubAudio: false });
    expect(params.dub).toBeUndefined();
    expect(params.voice).toBeUndefined();
    expect(params.engine).toBeUndefined();
  });

  it("automationPipelineSteps collapses the chain to one chunk job", () => {
    const steps = automationPipelineSteps(true, false, true);
    expect(steps).toEqual(["chunk"]);
  });

  it("automationPipelineSteps keeps the classic chain when chunked is off", () => {
    const steps = automationPipelineSteps(true, false, false);
    expect(steps).toEqual(["transcribe", "translate", "subtitle", "tts", "render"]);
  });

  it("the chunk plan derives real job status like any stage", () => {
    const plan = startPipelineWithSteps(["chunk"], {
      sourceLanguage: "",
      targetLanguage: "vi",
      provider: "gemini",
      dubAudio: false,
    });
    const submitted = markStageSubmitted(plan, "chunk", "job_1");
    const stages = deriveStages(submitted, [
      {
        id: "job_1",
        status: "running",
        progress: 0.42,
        stage: "chunk-stt",
        error_code: null,
        error_message: null,
      },
    ]);
    expect(stages[0].key).toBe("chunk");
    expect(stages[0].status).toBe("running");
    expect(stages[0].progress).toBe(0.42);
    expect(derivePhase(stages, submitted.startedAt)).toBe("running");
  });
});

describe("pipeline plan + derivation", () => {
  const jobs = (ids: Record<string, { status: string; progress?: number; stage?: string }>) =>
    Object.entries(ids).map(([id, j]) => ({
      id,
      status: j.status,
      progress: j.progress ?? 0,
      stage: j.stage ?? "",
      error_code: null,
      error_message: null,
    }));

  it("starts idle with all stages pending", () => {
    const plan = initialPipelinePlan();
    const stages = deriveStages(plan, []);
    expect(derivePhase(stages, plan.startedAt)).toBe("idle");
    expect(stages.every((s) => s.status === "pending")).toBe(true);
    expect(pipelineProgress(stages)).toBe(0);
  });

  it("startPipeline captures the run options", () => {
    const plan = startPipeline({
      sourceLanguage: "zh",
      targetLanguage: "vi",
      provider: "gemini",
      dubAudio: false,
    });
    expect(plan.options).toEqual({
      sourceLanguage: "zh",
      targetLanguage: "vi",
      provider: "gemini",
      dubAudio: false,
    });
    expect(plan.startedAt).toBeNull();
    expect(plan.stages.every((s) => s.jobId === null)).toBe(true);
    // Without dubbing the tts stage is skipped entirely.
    expect(plan.stages.map((s) => s.key)).toEqual([
      "transcribe",
      "translate",
      "subtitle",
      "render",
    ]);
  });

  it("startPipeline includes the tts stage when dubbing is enabled", () => {
    const plan = startPipeline({
      sourceLanguage: "zh",
      targetLanguage: "vi",
      provider: "gemini",
      dubAudio: true,
    });
    expect(plan.stages.map((s) => s.key)).toEqual([
      "transcribe",
      "translate",
      "subtitle",
      "tts",
      "render",
    ]);
  });

  it("tts stage params carry voice, engine and target language", () => {
    expect(buildStageParams("tts", OPTIONS)).toEqual({
      target_language: "vi",
      engine: "edge",
      voice: "vi-VN-HoaiMyNeural",
    });
    expect(
      buildStageParams("tts", {
        ...OPTIONS,
        dubAudio: true,
        voice: "vi-VN-NamMinhNeural",
        ttsEngine: "piper",
      }),
    ).toEqual({
      target_language: "vi",
      engine: "piper",
      voice: "vi-VN-NamMinhNeural",
    });
  });

  it("render requests the voice track when dubbing is on", () => {
    expect(buildStageParams("render", { ...OPTIONS, dubAudio: true })).toEqual({
      voice_track: "true",
    });
  });

  it("marks a submitted stage and derives running from the job snapshot", () => {
    let plan = initialPipelinePlan();
    plan = markStageSubmitted(plan, "transcribe", "job_0001");
    const stages = deriveStages(
      plan,
      jobs({ job_0001: { status: "running", progress: 0.5, stage: "transcribe" } }),
    );
    expect(derivePhase(stages, plan.startedAt)).toBe("running");
    expect(stages[0].status).toBe("running");
    expect(stages[0].progress).toBe(0.5);
    // 4-stage plan (no dubbing): transcribe slice is [0, 0.25]; half of it → 12.5%
    expect(pipelineProgress(stages)).toBeCloseTo(0.125, 5);
  });

  it("stays running between stages while the plan has started", () => {
    // transcribe succeeded, but translate is not yet submitted → the pipeline
    // must not flicker back to idle.
    let plan = initialPipelinePlan();
    plan = markStageSubmitted(plan, "transcribe", "job_0001");
    const stages = deriveStages(plan, jobs({ job_0001: { status: "succeeded", progress: 1 } }));
    expect(derivePhase(stages, plan.startedAt)).toBe("running");
    expect(pipelineProgress(stages)).toBeCloseTo(0.25, 5);
  });

  it("derives succeeded when every stage completed", () => {
    let plan = initialPipelinePlan();
    const order = ["transcribe", "translate", "subtitle", "render"] as const;
    order.forEach((key, i) => {
      plan = markStageSubmitted(plan, key, `job_000${i + 1}`);
    });
    const stages = deriveStages(
      plan,
      jobs({
        job_0001: { status: "succeeded", progress: 1 },
        job_0002: { status: "succeeded", progress: 1 },
        job_0003: { status: "succeeded", progress: 1 },
        job_0004: { status: "succeeded", progress: 1 },
      }),
    );
    expect(derivePhase(stages, plan.startedAt)).toBe("succeeded");
    expect(pipelineProgress(stages)).toBe(1);
  });

  it("derives failed and cancelled phases", () => {
    let plan = initialPipelinePlan();
    plan = markStageSubmitted(plan, "transcribe", "job_0001");
    const failed = deriveStages(plan, jobs({ job_0001: { status: "failed", progress: 0.3 } }));
    expect(derivePhase(failed, plan.startedAt)).toBe("failed");

    const cancelled = deriveStages(
      plan,
      jobs({ job_0001: { status: "cancelled", progress: 0.2 } }),
    );
    expect(derivePhase(cancelled, plan.startedAt)).toBe("cancelled");
  });

  it("ignores job events for stages it does not own", () => {
    const plan = initialPipelinePlan();
    const stages = deriveStages(plan, jobs({ job_9999: { status: "succeeded", progress: 1 } }));
    expect(stages.every((s) => s.status === "pending")).toBe(true);
  });
});

describe("languageLabel", () => {
  it("labels known codes and passes unknown codes through", () => {
    expect(languageLabel("vi")).toBe("Vietnamese");
    expect(languageLabel("zh")).toBe("Chinese");
    expect(languageLabel("")).toBe("Auto Detect");
    expect(languageLabel("xx")).toBe("xx");
  });
});
