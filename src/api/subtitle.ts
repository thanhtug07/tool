import { safeInvoke } from "@/api/invoke";

export type CueStatus = "draft" | "translated" | "edited" | "approved";

/** One persistent subtitle cue row (project-scoped, ordered by `cue_number`). */
export type SubtitleCue = {
  id: string;
  project_id: string;
  cue_number: number;
  /** Start time in seconds. */
  start: number;
  /** End time in seconds. */
  end: number;
  /** Target subtitle text (edited by the user). */
  text: string;
  speaker: string | null;
  source_text: string | null;
  status: CueStatus;
  style_json: string | null;
  updated_at: string;
};

/** Editor patch — any subset of the user-editable fields. */
export type CuePatch = {
  start?: number;
  end?: number;
  text?: string;
  speaker?: string;
  status?: CueStatus;
};

/** Cue as supplied by the pipeline import (server assigns id/status). */
export type SubtitleCueInput = {
  cue_number: number;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
  source_text?: string | null;
};

export function getSubtitleCues(projectId: string): Promise<SubtitleCue[]> {
  return safeInvoke("subtitle.get_cues", { projectId });
}

export function replaceSubtitleCues(projectId: string, cues: SubtitleCueInput[]): Promise<number> {
  return safeInvoke("subtitle.replace_cues", { projectId, cues });
}

export function updateSubtitleCue(id: string, patch: CuePatch): Promise<SubtitleCue> {
  return safeInvoke("subtitle.update_cue", { id, patch });
}
