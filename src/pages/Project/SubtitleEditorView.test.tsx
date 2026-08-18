import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/api/subtitle", () => ({
  getSubtitleCues: vi.fn(),
  updateSubtitleCue: vi.fn(),
  replaceSubtitleCues: vi.fn(),
}));

import SubtitleEditorView, {
  CueTable,
  editorReducer,
  filterCues,
  formatTime,
  initialEditorState,
} from "./SubtitleEditorView";
import type { EditorState } from "./SubtitleEditorView";
import type { SubtitleCue } from "@/api/subtitle";

const PROJECT = "00000000-0000-4000-8000-000000000000";

function cue(id: string, cueNumber: number, text = "dòng", start = 0.0, end = 1.0): SubtitleCue {
  return {
    id,
    project_id: PROJECT,
    cue_number: cueNumber,
    start,
    end,
    text,
    speaker: text === "dòng" ? "Nam" : null,
    source_text: text === "dòng" ? "Hello" : null,
    status: "draft",
    style_json: null,
    updated_at: "t",
  };
}

function seedCues(count: number): SubtitleCue[] {
  return Array.from({ length: count }, (_, i) => cue(String(i + 1), i + 1));
}

function baseState(): EditorState {
  return { ...initialEditorState, cues: seedCues(3) };
}

describe("formatTime", () => {
  it("formats seconds as m:ss.mmm", () => {
    expect(formatTime(3.2)).toBe("0:03.200");
    expect(formatTime(65.5)).toBe("1:05.500");
    expect(formatTime(0)).toBe("0:00.000");
  });

  it("clamps negative input", () => {
    expect(formatTime(-1)).toBe("0:00.000");
  });
});

describe("filterCues", () => {
  const cues: SubtitleCue[] = [
    { ...cue("1", 1, "Xin chào", 0, 1), speaker: "Nam" },
    { ...cue("2", 2, "Tạm biệt", 1, 2), speaker: "Lan" },
  ];

  it("returns everything for an empty query", () => {
    expect(filterCues(cues, "  ")).toHaveLength(2);
  });

  it("matches by transcript number", () => {
    expect(filterCues(cues, "2").map((c) => c.id)).toEqual(["2"]);
  });

  it("matches source text and speaker case-insensitively", () => {
    expect(filterCues([cue("5", 5, "dòng", 0, 1)], "hello")).toHaveLength(1);
    expect(filterCues(cues, "nam")).toHaveLength(1);
  });
});

describe("editorReducer", () => {
  it("loads a cue set, resetting any prior edits", () => {
    const state = editorReducer(baseState(), { type: "load", cues: [] });
    expect(state.cues).toEqual([]);
    expect(state.undoStack).toEqual([]);
    expect(state.pending).toBeNull();
  });

  it("begin-edit populates drafts from the cue; set-draft updates them", () => {
    let state = editorReducer(baseState(), { type: "begin-edit", id: "2" });
    expect(state.editingId).toBe("2");
    expect(state.drafts.text).toBe("dòng");
    state = editorReducer(state, { type: "set-draft", field: "text", value: "đã sửa" });
    expect(state.drafts.text).toBe("đã sửa");
  });

  it("commit-edit with empty translation is rejected", () => {
    let state = editorReducer(baseState(), { type: "begin-edit", id: "2" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "   " });
    const next = editorReducer(state, { type: "commit-edit" });
    expect(next.pending).toBeNull();
    expect(next.undoStack).toHaveLength(0);
  });

  it("commit-edit builds a patch, sets status edited and records an undo", () => {
    let state = editorReducer(baseState(), { type: "begin-edit", id: "2" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "đã sửa" });
    state = editorReducer(state, { type: "set-draft", field: "start", value: "2.5" });
    state = editorReducer(state, { type: "set-draft", field: "end", value: "3.0" });
    const next = editorReducer(state, { type: "commit-edit" });
    expect(next.editingId).toBeNull();
    expect(next.pending).toEqual({
      id: "2",
      patch: { start: 2.5, end: 3.0, text: "đã sửa", speaker: "Nam", status: "edited" },
      prev: expect.objectContaining({ id: "2" }),
      undo: false,
    });
    expect(next.undoStack).toHaveLength(1);
  });

  it("save-ok applies the persisted row and clears the pending save", () => {
    let state = editorReducer(baseState(), { type: "begin-edit", id: "2" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "đã sửa" });
    state = editorReducer(state, { type: "commit-edit" });
    const saved = { ...state.cues[1], text: "đã sửa", status: "edited" as const };
    const next = editorReducer(state, { type: "save-ok", saved, pending: state.pending! });
    expect(next.pending).toBeNull();
    expect(next.cues[1].text).toBe("đã sửa");
    expect(next.cues[1].status).toBe("edited");
  });

  it("save-ok for a stale in-flight save keeps a newer pending edit", () => {
    let state = editorReducer(baseState(), { type: "begin-edit", id: "2" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "đã sửa" });
    state = editorReducer(state, { type: "commit-edit" });
    const stalePending = state.pending!;
    // The user edits again while the first save is in flight.
    state = editorReducer(state, { type: "begin-edit", id: "3" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "dòng mới" });
    state = editorReducer(state, { type: "commit-edit" });
    const saved = { ...state.cues[2], text: "dòng mới", status: "edited" as const };
    const next = editorReducer(state, { type: "save-ok", saved, pending: stalePending });
    // The stale save resolved but must not wipe the newer pending edit.
    expect(next.pending).not.toBeNull();
    expect(next.pending?.patch.text).toBe("dòng mới");
  });

  it("undo restores the previous cue and schedules an immediate re-save", () => {
    const before = { ...baseState().cues[1], status: "edited" as const };
    let state = editorReducer(baseState(), {
      type: "load",
      cues: [baseState().cues[0], before, baseState().cues[2]],
    });
    state = editorReducer(state, { type: "begin-edit", id: "2" });
    state = editorReducer(state, { type: "set-draft", field: "text", value: "sửa" });
    state = editorReducer(state, { type: "commit-edit" });
    state = editorReducer(state, {
      type: "save-ok",
      saved: { ...before, text: "sửa", status: "edited" },
      pending: state.pending!,
    });
    const undone = editorReducer(state, { type: "undo" });
    expect(undone.undoStack).toHaveLength(0);
    expect(undone.pending?.undo).toBe(true);
    expect(undone.pending?.patch.text).toBe("dòng");
    expect(undone.pending?.patch.status).toBe("edited");
  });

  it("undo with an empty stack is a no-op", () => {
    const state = editorReducer(baseState(), { type: "undo" });
    expect(state.pending).toBeNull();
    expect(state.cues).toHaveLength(3);
  });
});

describe("SubtitleEditorView (unit — mocked bridge, static render)", () => {
  it("renders the editor shell with project selector and empty state", () => {
    const html = renderToStaticMarkup(<SubtitleEditorView />);
    expect(html).toContain("Subtitle Editor");
    expect(html).toContain("Project ID");
    expect(html).toContain("Undo");
    expect(html).toContain("No cues to display.");
  });
});

describe("CueTable virtualization", () => {
  it("renders only a window of rows while reporting the true total", () => {
    const many = seedCues(1200);
    const html = renderToStaticMarkup(
      <CueTable
        cues={many}
        editingId={null}
        drafts={{ text: "", start: "", end: "", speaker: "" }}
        onBeginEdit={() => undefined}
        onSetDraft={() => undefined}
        onCommitEdit={() => undefined}
        onCancelEdit={() => undefined}
        onDelete={() => undefined}
      />,
    );
    const totalCues = /data-total-cues="(\d+)"/.exec(html);
    expect(totalCues).not.toBeNull();
    expect(Number(totalCues?.[1])).toBe(1200);
    const renderedRows = html.match(/data-role="cue-row"/g) ?? [];
    expect(renderedRows.length).toBeLessThan(1200);
    expect(renderedRows.length).toBeGreaterThan(0);
  });

  it("preserves cue numbers across a row window", () => {
    const html = renderToStaticMarkup(
      <CueTable
        cues={seedCues(1000)}
        editingId={null}
        drafts={{ text: "", start: "", end: "", speaker: "" }}
        onBeginEdit={() => undefined}
        onSetDraft={() => undefined}
        onCommitEdit={() => undefined}
        onCancelEdit={() => undefined}
        onDelete={() => undefined}
      />,
    );
    expect(html).toContain('data-cue-number="1"');
    expect(html).toContain('data-total-cues="1000"');
  });
});
