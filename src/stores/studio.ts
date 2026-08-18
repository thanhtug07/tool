import { useSyncExternalStore } from "react";

import type { PipelinePhase } from "@/pages/Automation/automation";

/**
 * Live pipeline status shared between the StudioWorkspace and the global
 * TopBar (which renders the "Processing 68%" state and the primary Export
 * action). The workspace pushes its real phase/progress/export capability
 * up here; the top bar only reads it — the two can never drift.
 */
export type StudioStatus = {
  phase: PipelinePhase;
  overallProgress: number;
  canExport: boolean;
  /** Current export trigger (the workspace owns artifacts + dialog flow). */
  export: (() => void) | null;
  /** Cancel the running automation (the workspace owns the job cancel flow). */
  cancel: (() => void) | null;
  /** Undo the last cue edit (frontend history stack over the cue backend). */
  undo: (() => void) | null;
  /** Redo the most recently undone cue edit. */
  redo: (() => void) | null;
};

const IDLE: StudioStatus = {
  phase: "idle",
  overallProgress: 0,
  canExport: false,
  export: null,
  cancel: null,
  undo: null,
  redo: null,
};

let snapshot: StudioStatus = IDLE;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): StudioStatus {
  return snapshot;
}

export function setStudioStatus(status: StudioStatus) {
  snapshot = status;
  emit();
}

export function useStudioStatus(): StudioStatus {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
