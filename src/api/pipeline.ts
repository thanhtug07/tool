import { safeInvoke } from "@/api/invoke";

/** Canonical per-project artifact paths (RELEASE-P0-005). */
export type ArtifactPaths = {
  projectDir: string;
  audio: string;
  transcript: string;
  translation: string;
  subtitleSrt: string;
  subtitleAss: string;
  renderedVideo: string;
};

/**
 * Absolute paths of the project's pipeline artifacts (read-only metadata).
 * Used by the workflow UI to preview the source video, hand paths to the
 * export commands, and check stage outputs.
 */
export function getArtifactPaths(projectId: string): Promise<ArtifactPaths> {
  return safeInvoke("pipeline.artifact_paths", { projectId });
}

export function submitPipeline(
  projectId: string,
  params: Record<string, unknown>,
): Promise<import("./job").Job> {
  return safeInvoke("pipeline.submit", { projectId, params });
}
