import type { CuePatch, SubtitleCue } from "@/api/subtitle";

/** Max undo entries kept per project session (memory-bounded). */
export const MAX_UNDO_ENTRIES = 50;

/**
 * Push a pre-change snapshot onto the undo stack (newest last), capped at
 * `MAX_UNDO_ENTRIES` so a long editing session cannot grow without bound.
 */
export function pushUndo(stack: SubtitleCue[][], snapshot: SubtitleCue[]): SubtitleCue[][] {
  return [...stack, snapshot].slice(-MAX_UNDO_ENTRIES);
}

/** Editable fields undo/redo restores (matches what the workspace edits). */
const EDITABLE_FIELDS: (keyof CuePatch)[] = ["start", "end", "text"];

/**
 * Compute the per-cue patches needed to turn `current` into `target`.
 *
 * Cue ids are stable across edits, so each changed cue maps to a
 * `subtitle.update_cue` call (the backend has no delete, so a cue missing
 * from `target` is skipped rather than dropped). Returns an empty array when
 * the states already match.
 */
export function diffCues(
  current: SubtitleCue[],
  target: SubtitleCue[],
): { id: string; patch: CuePatch }[] {
  const targetById = new Map(target.map((cue) => [cue.id, cue]));
  const patches: { id: string; patch: CuePatch }[] = [];
  for (const targetCue of targetById.values()) {
    const currentCue = current.find((cue) => cue.id === targetCue.id);
    if (!currentCue) continue;
    const patch: CuePatch = {};
    for (const field of EDITABLE_FIELDS) {
      if (currentCue[field] !== targetCue[field]) {
        (patch as Record<string, unknown>)[field] = targetCue[field];
      }
    }
    if (Object.keys(patch).length > 0) {
      patches.push({ id: targetCue.id, patch });
    }
  }
  return patches;
}
