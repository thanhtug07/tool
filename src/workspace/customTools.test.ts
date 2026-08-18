import { describe, expect, it } from "vitest";

import {
  buildStepsFromTools,
  stageMeta,
  stagesForTools,
  toolDef,
  toolsShareStage,
  CUSTOM_TOOLS,
  type ActiveCustomTool,
} from "./customTools";

function tool(
  id: ActiveCustomTool["id"],
  config: ActiveCustomTool["config"] = {},
): ActiveCustomTool {
  return { id, config };
}

describe("customTools", () => {
  it("resolves one tool to its real backend stages in dependency order", () => {
    expect(stagesForTools([tool("audio-separate")])).toEqual(["audio"]);
    expect(stagesForTools([tool("logo-remove")])).toEqual(["logo", "render"]);
    expect(stagesForTools([tool("burn-subtitles")])).toEqual(["subtitle", "render"]);
    // Dub = full pipeline through render.
    expect(stagesForTools([tool("dub")])).toEqual([
      "transcribe",
      "translate",
      "subtitle",
      "tts",
      "render",
    ]);
  });

  it("merges multiple tools into one dependency-ordered, deduplicated run", () => {
    // Translate + logo: transcribe → translate → subtitle → logo → render.
    expect(stagesForTools([tool("translate-video"), tool("logo-remove")])).toEqual([
      "transcribe",
      "translate",
      "subtitle",
      "logo",
      "render",
    ]);
    // Audio + burn-subtitles shares subtitle/render (dedup).
    expect(stagesForTools([tool("burn-subtitles"), tool("audio-separate")])).toEqual([
      "subtitle",
      "audio",
      "render",
    ]);
  });

  it("applies audio mode + logo region as real step configs", () => {
    const steps = buildStepsFromTools([
      tool("audio-separate", { audioMode: "denoise" }),
      tool("logo-remove", { logo: { x: 10, y: 20, width: 120, height: 90 } }),
    ]);
    const audio = steps.find((s) => s.id === "audio");
    const logo = steps.find((s) => s.id === "logo");
    expect(audio?.config).toEqual({ mode: "denoise" });
    expect(logo?.config).toEqual({ x: 10, y: 20, width: 120, height: 90 });
  });

  it("carries each tool's own provider into its translate stage", () => {
    // Two tools both translate — the merged stage keeps the provider of the
    // FIRST tool that owns it (system-decided, stable), not the last applied.
    const steps = buildStepsFromTools([
      tool("translate-video", { translateProvider: "gemini" }),
      tool("dub", { dubTargetLanguage: "en", translateProvider: "free" }),
    ]);
    const translate = steps.find((s) => s.id === "translate");
    expect(translate?.config).toEqual({ provider: "gemini" });
  });

  it("never reorders stages by user choice — system dependency order wins", () => {
    // Even if a user "applies" logo before audio, the engine order stays fixed.
    expect(stagesForTools([tool("logo-remove"), tool("audio-separate")])).toEqual([
      "audio",
      "logo",
      "render",
    ]);
  });

  it("toolDef throws on unknown ids", () => {
    expect(() => toolDef("nope" as never)).toThrow(/unknown custom tool/);
  });

  it("toolsShareStage detects shared backend stages", () => {
    expect(toolsShareStage(tool("translate-video"), tool("burn-subtitles"))).toBe(true);
    expect(toolsShareStage(tool("audio-separate"), tool("logo-remove"))).toBe(false);
  });

  it("buildStepsFromTools enables every mapped stage", () => {
    const steps = buildStepsFromTools([tool("translate-video")]);
    expect(steps.map((s) => s.id)).toEqual(["transcribe", "translate", "subtitle", "render"]);
    expect(steps.every((s) => s.enabled)).toBe(true);
  });

  it("stageMeta provides a display label + icon for every pipeline stage", () => {
    // The pipeline preview renders one chip per stage — every stage the tools
    // can run must have metadata or the chain would render a blank chip.
    const allStages = new Set<string>();
    for (const t of CUSTOM_TOOLS) for (const s of t.stages) allStages.add(s);
    for (const key of allStages) {
      const meta = stageMeta(key as Parameters<typeof stageMeta>[0]);
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.icon).toBeTruthy();
    }
  });
});
