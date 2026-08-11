import { useState } from "react";

import { getSubtitleCues } from "@/api/subtitle";
import type { SubtitleCue } from "@/api/subtitle";
import { toMediaUrl } from "@/api/media";
import VideoPreview from "@/components/VideoPreview";

const EMPTY_PROJECT = "00000000-0000-4000-8000-000000000000";

/** Preview page (TASK-026): stream a project video and overlay its cues. */
export default function PreviewView() {
  const [projectId, setProjectId] = useState(EMPTY_PROJECT);
  const [videoPath, setVideoPath] = useState("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [cues, setCues] = useState<SubtitleCue[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const loaded = await getSubtitleCues(projectId);
      setCues(loaded);
      setVideoUrl(videoPath.trim() ? toMediaUrl(videoPath.trim()) : null);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <section aria-labelledby="preview-heading" className="space-y-3">
      <h1 id="preview-heading" className="text-lg font-semibold">
        Preview
      </h1>
      <p className="text-sm text-muted-foreground">
        Stream the project source video and check the caption overlay against the ASS-default style
        before rendering.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="preview-project-id" className="text-sm">
          Project ID
        </label>
        <input
          id="preview-project-id"
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
        />
        <label htmlFor="preview-video-path" className="text-sm">
          Video path
        </label>
        <input
          id="preview-video-path"
          className="w-80 rounded border border-border bg-background px-2 py-1 text-sm"
          placeholder="C:\Videos\clip.mp4"
          value={videoPath}
          onChange={(event) => setVideoPath(event.target.value)}
        />
        <button
          type="button"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          onClick={() => void load()}
        >
          Load
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {videoUrl ? (
        <VideoPreview videoUrl={videoUrl} cues={cues} />
      ) : (
        <p data-role="preview-empty" className="text-sm text-muted-foreground">
          Enter a project ID and video path to preview the subtitle overlay.
        </p>
      )}
    </section>
  );
}
