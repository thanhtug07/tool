import { useEffect, useReducer, useRef, useState } from "react";

import { getSubtitleCues, updateSubtitleCue } from "@/api/subtitle";
import type { CuePatch, SubtitleCue } from "@/api/subtitle";

const EMPTY_PROJECT = "00000000-0000-4000-8000-000000000000";
const SAVE_DEBOUNCE_MS = 600;
const ROW_HEIGHT = 64;
const VIEWPORT_HEIGHT = 480;
const UNDO_LIMIT = 50;

export type DraftFields = { text: string; start: string; end: string; speaker: string };

export type CuePatchPending = {
  id: string;
  patch: CuePatch;
  prev: SubtitleCue;
  undo: boolean;
};

export type EditorState = {
  cues: SubtitleCue[];
  editingId: string | null;
  drafts: DraftFields;
  pending: CuePatchPending | null;
  undoStack: { id: string; prev: SubtitleCue }[];
};

export const initialEditorState: EditorState = {
  cues: [],
  editingId: null,
  drafts: { text: "", start: "", end: "", speaker: "" },
  pending: null,
  undoStack: [],
};

export type EditorAction =
  | { type: "load"; cues: SubtitleCue[] }
  | { type: "begin-edit"; id: string }
  | { type: "cancel-edit" }
  | { type: "set-draft"; field: keyof DraftFields; value: string }
  | { type: "commit-edit" }
  | { type: "save-ok"; saved: SubtitleCue }
  | { type: "undo" };

function parseSeconds(field: string): number | null {
  const value = Number(field);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "load":
      return { ...initialEditorState, cues: action.cues };
    case "begin-edit": {
      const cue = state.cues.find((c) => c.id === action.id);
      if (!cue || cue.id === state.editingId) return state;
      return {
        ...state,
        editingId: cue.id,
        drafts: {
          text: cue.text,
          start: String(cue.start),
          end: String(cue.end),
          speaker: cue.speaker ?? "",
        },
      };
    }
    case "cancel-edit":
      return { ...state, editingId: null };
    case "set-draft":
      if (!state.editingId) return state;
      return { ...state, drafts: { ...state.drafts, [action.field]: action.value } };
    case "commit-edit": {
      const cue = state.cues.find((c) => c.id === state.editingId);
      if (!cue) return state;
      const text = state.drafts.text.trim();
      if (!text) return state;
      const start = parseSeconds(state.drafts.start) ?? cue.start;
      const end = parseSeconds(state.drafts.end) ?? cue.end;
      if (end < start) return state;
      const patch: CuePatch = {
        start,
        end,
        text,
        speaker: state.drafts.speaker,
        status: "edited",
      };
      const next = [...state.undoStack, { id: cue.id, prev: cue }].slice(-UNDO_LIMIT);
      return {
        ...state,
        editingId: null,
        pending: { id: cue.id, patch, prev: cue, undo: false },
        undoStack: next,
      };
    }
    case "save-ok": {
      const { saved } = action;
      return {
        ...state,
        cues: state.cues.map((c) => (c.id === saved.id ? saved : c)),
        pending: null,
      };
    }
    case "undo": {
      if (state.undoStack.length === 0) return state;
      const { id, prev } = state.undoStack[state.undoStack.length - 1];
      const current = state.cues.find((c) => c.id === id);
      if (!current) return state;
      const patch: CuePatch = {
        start: prev.start,
        end: prev.end,
        text: prev.text,
        speaker: prev.speaker ?? "",
        status: prev.status,
      };
      return {
        ...state,
        editingId: null,
        pending: { id, patch, prev: current, undo: true },
        undoStack: state.undoStack.slice(0, -1),
      };
    }
    default:
      return state;
  }
}

export function formatTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const minutes = Math.floor(s / 60);
  const totalMs = Math.round((s % 60) * 1000);
  const sec = Math.floor(totalMs / 1000);
  const frac = String(totalMs % 1000).padStart(3, "0");
  return `${minutes}:${String(sec).padStart(2, "0")}.${frac}`;
}

export function filterCues(cues: SubtitleCue[], query: string): SubtitleCue[] {
  const q = query.trim().toLowerCase();
  if (!q) return cues;
  return cues.filter((cue) =>
    [String(cue.cue_number), cue.speaker ?? "", cue.source_text ?? "", cue.text].some((field) =>
      field.toLowerCase().includes(q),
    ),
  );
}

const ROW_EARLY_OVERSCAN = 2;

/** Presentational, windowed cue table — renders only the visible slice. */
export function CueTable(props: {
  cues: SubtitleCue[];
  editingId: string | null;
  drafts: DraftFields;
  onBeginEdit: (id: string) => void;
  onSetDraft: (field: keyof DraftFields, value: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
}) {
  const { cues, editingId, drafts } = props;
  const [scrollTop, setScrollTop] = useState(0);

  const totalHeight = cues.length * ROW_HEIGHT;
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - ROW_EARLY_OVERSCAN);
  const visible = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + ROW_EARLY_OVERSCAN * 2 + 1;
  const windowCues = cues.slice(start, start + visible);

  return (
    <div
      data-role="cue-table"
      data-total-cues={cues.length}
      className="mt-3 max-h-[480px] overflow-y-auto rounded border border-border"
      style={{ height: VIEWPORT_HEIGHT }}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
    >
      {cues.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No cues to display.</p>
      ) : (
        <div style={{ height: totalHeight, position: "relative" }}>
          {windowCues.map((cue, index) => (
            <div
              key={cue.id}
              data-role="cue-row"
              data-cue-number={cue.cue_number}
              className="absolute left-0 right-0 flex items-start gap-3 border-b border-border px-3 py-2"
              style={{ top: (start + index) * ROW_HEIGHT, height: ROW_HEIGHT }}
            >
              <span className="w-10 shrink-0 text-xs tabular-nums text-muted-foreground">
                {cue.cue_number}
              </span>
              {editingId === cue.id ? (
                <>
                  <label className="w-24 shrink-0">
                    <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                      Start
                    </span>
                    <input
                      aria-label="start"
                      className="w-full rounded border border-border bg-background px-1.5 py-1 text-xs"
                      value={drafts.start}
                      onChange={(event) => props.onSetDraft("start", event.target.value)}
                    />
                  </label>
                  <label className="w-24 shrink-0">
                    <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                      End
                    </span>
                    <input
                      aria-label="end"
                      className="w-full rounded border border-border bg-background px-1.5 py-1 text-xs"
                      value={drafts.end}
                      onChange={(event) => props.onSetDraft("end", event.target.value)}
                    />
                  </label>
                  <label className="w-24 shrink-0">
                    <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                      Speaker
                    </span>
                    <input
                      aria-label="speaker"
                      className="w-full rounded border border-border bg-background px-1.5 py-1 text-xs"
                      value={drafts.speaker}
                      onChange={(event) => props.onSetDraft("speaker", event.target.value)}
                    />
                  </label>
                  <div className="flex-1">
                    <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                      Translation
                    </span>
                    <textarea
                      aria-label="translation"
                      className="h-9 w-full resize-none rounded border border-border bg-background px-1.5 py-1 text-xs"
                      value={drafts.text}
                      onChange={(event) => props.onSetDraft("text", event.target.value)}
                    />
                  </div>
                  <div className="flex shrink-0 items-center gap-1 pt-4">
                    <button
                      type="button"
                      onClick={props.onCommitEdit}
                      className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={props.onCancelEdit}
                      className="rounded border border-border px-2 py-1 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span
                    className="w-48 shrink-0 text-xs text-muted-foreground"
                    title={`Timestamp: ${cue.start} – ${cue.end}s`}
                  >
                    {formatTime(cue.start)} → {formatTime(cue.end)}
                  </span>
                  <span className="w-24 shrink-0 truncate text-xs text-muted-foreground">
                    {cue.speaker ?? ""}
                  </span>
                  <span
                    className="w-40 shrink-0 truncate text-xs text-muted-foreground"
                    title={cue.source_text ?? undefined}
                  >
                    {cue.source_text ?? ""}
                  </span>
                  <span className="flex-1 text-sm whitespace-pre-line">{cue.text}</span>
                  <span className="shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
                    {cue.status}
                  </span>
                  <span className="sr-only">Double-click to edit</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SubtitleEditorView() {
  const [state, dispatch] = useReducer(editorReducer, initialEditorState);
  const [projectId, setProjectId] = useState(EMPTY_PROJECT);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<number | null>(null);

  async function persist(pending: CuePatchPending) {
    const { id, patch } = pending;
    setSaving(true);
    try {
      const saved = await updateSubtitleCue(id, patch);
      dispatch({ type: "save-ok", saved });
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  function flush() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (state.pending) {
      void persist(state.pending);
    }
  }

  function schedule() {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (state.pending) {
        void persist(state.pending);
      }
    }, SAVE_DEBOUNCE_MS);
  }

  useEffect(() => {
    if (!state.pending) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    if (state.pending.undo) {
      void persist(state.pending);
    } else {
      schedule();
    }
  }, [state.pending]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        flush();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [state.pending, state.undoStack]);

  async function load() {
    setError(null);
    try {
      const cues = await getSubtitleCues(projectId);
      dispatch({ type: "load", cues });
    } catch (e) {
      setError(String(e));
    }
  }

  const visibleCues = filterCues(state.cues, query);
  const editingDrafts = state.editingId
    ? state.drafts
    : { text: "", start: "", end: "", speaker: "" };

  return (
    <section aria-labelledby="subtitle-editor-heading" className="space-y-3">
      <h1 id="subtitle-editor-heading" className="text-lg font-semibold">
        Subtitle Editor
      </h1>
      <p className="text-sm text-muted-foreground">
        Review and edit the translated cue list before rendering. Saves are debounced automatically;
        Ctrl+S saves immediately.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="sub-project-id" className="text-sm">
          Project ID
        </label>
        <input
          id="sub-project-id"
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
        />
        <button
          type="button"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          onClick={() => void load()}
        >
          Load guides
        </button>
        <input
          aria-label="Filter cues"
          placeholder="filter cue number, speaker, source or text"
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={state.undoStack.length === 0}
          onClick={() => dispatch({ type: "undo" })}
          className="rounded border border-border px-3 py-1 text-sm disabled:opacity-40"
        >
          Undo
        </button>
        <button
          type="button"
          disabled={!state.pending || saving}
          onClick={flush}
          className="rounded border border-border px-3 py-1 text-sm disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save pending"}
        </button>
        {error && <span className="text-sm text-destructive">{error}</span>}
        <span className="ml-auto text-xs text-muted-foreground">
          {state.cues.length} cue{state.cues.length === 1 ? "" : "s"}
        </span>
      </div>

      {state.editingId ? (
        <div className="flex justify-end gap-1">
          <button
            type="button"
            onClick={() => dispatch({ type: "commit-edit" })}
            className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          >
            Commit edit
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: "cancel-edit" })}
            className="rounded border border-border px-3 py-1 text-sm"
          >
            Cancel
          </button>
        </div>
      ) : null}

      <CueTable
        cues={visibleCues}
        editingId={state.editingId}
        drafts={editingDrafts}
        onBeginEdit={(id) => dispatch({ type: "begin-edit", id })}
        onSetDraft={(field, value) => dispatch({ type: "set-draft", field, value })}
        onCommitEdit={() => dispatch({ type: "commit-edit" })}
        onCancelEdit={() => dispatch({ type: "cancel-edit" })}
      />

      <p className="text-xs text-muted-foreground">
        Double-click a cue row to edit its timing, speaker and translation.
      </p>
    </section>
  );
}
