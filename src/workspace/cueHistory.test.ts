import { describe, expect, it } from "vitest";

import type { SubtitleCue } from "@/api/subtitle";
import { MAX_UNDO_ENTRIES, diffCues, pushUndo } from "./cueHistory";

function cue(id: string, over: Partial<SubtitleCue> = {}): SubtitleCue {
  return {
    id,
    project_id: "p1",
    cue_number: Number(id.replace("c", "")),
    start: 0,
    end: 1,
    text: "text " + id,
    speaker: null,
    source_text: null,
    status: "translated",
    style_json: null,
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("pushUndo", () => {
  it("appends the snapshot (newest last)", () => {
    const a = [cue("c0")];
    const b = [cue("c0", { text: "edited" })];
    const stack = pushUndo([a], b);
    expect(stack).toHaveLength(2);
    expect(stack[1]).toBe(b);
  });

  it("is bounded by MAX_UNDO_ENTRIES", () => {
    let stack: SubtitleCue[][] = [];
    for (let i = 0; i < MAX_UNDO_ENTRIES + 10; i++) {
      stack = pushUndo(stack, [cue(`c${i}`)]);
    }
    expect(stack).toHaveLength(MAX_UNDO_ENTRIES);
    // Oldest entries fall off; the newest are kept.
    expect(stack[0][0].id).toBe(`c10`);
    expect(stack[MAX_UNDO_ENTRIES - 1][0].id).toBe(`c${MAX_UNDO_ENTRIES + 9}`);
  });
});

describe("diffCues", () => {
  it("returns an empty list when nothing changed", () => {
    const cues = [cue("c0"), cue("c1")];
    expect(diffCues(cues, cues)).toEqual([]);
  });

  it("detects a text edit", () => {
    const current = [cue("c0", { text: "old" })];
    const target = [cue("c0", { text: "new" })];
    expect(diffCues(current, target)).toEqual([{ id: "c0", patch: { text: "new" } }]);
  });

  it("detects timestamp edits (start + end)", () => {
    const current = [cue("c0", { start: 1, end: 2 })];
    const target = [cue("c0", { start: 3, end: 4 })];
    expect(diffCues(current, target)).toEqual([{ id: "c0", patch: { start: 3, end: 4 } }]);
  });

  it("only includes the fields that changed", () => {
    const current = [cue("c0", { start: 1, end: 2, text: "a" })];
    const target = [cue("c0", { start: 1, end: 9, text: "a" })];
    expect(diffCues(current, target)).toEqual([{ id: "c0", patch: { end: 9 } }]);
  });

  it("skips cues that no longer exist (no delete API) and ignores unknowns", () => {
    const current = [cue("c0"), cue("c1")];
    const target = [cue("c0", { text: "changed" }), cue("ghost")];
    expect(diffCues(current, target)).toEqual([{ id: "c0", patch: { text: "changed" } }]);
  });
});
