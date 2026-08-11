import { describe, expect, it } from "vitest";

import type { SubtitleCue } from "@/api/subtitle";
import { ASS_DEFAULT_STYLE, activeCue, captionStyle, cssPx, scalePlayRes } from "./subtitleOverlay";

function cue(start: number, end: number, cueNumber = start + 1): SubtitleCue {
  return {
    id: String(cueNumber),
    project_id: "00000000-0000-4000-8000-000000000000",
    cue_number: cueNumber,
    start,
    end,
    text: `cue ${cueNumber}`,
    speaker: null,
    source_text: null,
    status: "draft",
    style_json: null,
    updated_at: "t",
  };
}

describe("scalePlayRes / cssPx", () => {
  it("keeps ASS PlayRes values at a 1080p element", () => {
    expect(scalePlayRes(44, 1080)).toBe(44);
    expect(scalePlayRes(24, 1080)).toBe(24);
  });

  it("scales down for a smaller video height", () => {
    expect(scalePlayRes(44, 540)).toBe(22);
    expect(scalePlayRes(24, 270)).toBe(6);
  });

  it("formats stable one-decimal pixel strings", () => {
    expect(cssPx(44)).toBe("44px");
    expect(cssPx(22.5)).toBe("22.5px");
  });
});

describe("activeCue boundary semantics ([start, end))", () => {
  const cues = [cue(1, 2, 1), cue(2, 3, 2), cue(5, 6, 3)];

  it("picks the cue whose start is inclusive", () => {
    expect(activeCue(cues, 1)?.cue_number).toBe(1);
    expect(activeCue(cues, 2)?.cue_number).toBe(2);
  });

  it("treats end as exclusive — the next cue owns that instant", () => {
    expect(activeCue(cues, 1.999)?.cue_number).toBe(1);
    expect(activeCue(cues, 2)?.cue_number).toBe(2);
    expect(activeCue(cues, 2.999)?.cue_number).toBe(2);
    expect(activeCue(cues, 3)).toBeNull();
  });

  it("returns null just before a cue, after a gap and past the last cue", () => {
    expect(activeCue(cues, 0.999)).toBeNull();
    expect(activeCue(cues, 3.001)).toBeNull();
    expect(activeCue(cues, 6.001)).toBeNull();
    expect(activeCue(cues, -1)).toBeNull();
  });

  it("returns null for an empty cue list", () => {
    expect(activeCue([], 1)).toBeNull();
  });
});

describe("captionStyle parity with ASS defaults", () => {
  it("maps font size, safe-area margins and stroke/shadow at 1080p", () => {
    const style = captionStyle(ASS_DEFAULT_STYLE, 1080);
    expect(style.fontSize).toBe("44px");
    expect(style.fontFamily).toBe("Arial");
    expect(style.bottom).toBe("24px");
    expect(style.left).toBe("10px");
    expect(style.right).toBe("10px");
    expect(style.textAlign).toBe("center");
    expect(style.color).toBe("#fff");
    // 8 outline directions + one drop shadow (rgba commas must not be counted).
    const shadow = style.textShadow ?? "";
    expect((shadow.match(/#000/g) ?? []).length).toBe(8);
    expect(shadow).toContain("rgba(0, 0, 0, 0.5)");
    expect(shadow).toContain("-2px -2px 0 #000");
  });

  it("honours the top-center position", () => {
    const style = captionStyle({ ...ASS_DEFAULT_STYLE, position: "top_center" }, 1080);
    expect(style.top).toBe("24px");
    expect(style.bottom).toBe("auto");
  });

  it("adds a background box when bgBox is on", () => {
    const style = captionStyle({ ...ASS_DEFAULT_STYLE, bgBox: true }, 1080);
    expect(style.backgroundColor).toBe("rgba(0, 0, 0, 0.5)");
    expect(style.padding).toBeTruthy();
  });

  it("scales the whole overlay to the rendered video height", () => {
    const style = captionStyle(ASS_DEFAULT_STYLE, 540);
    expect(style.fontSize).toBe("22px");
    expect(style.bottom).toBe("12px");
  });
});
