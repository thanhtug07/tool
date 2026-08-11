import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import VideoPreview, { formatTime } from "./VideoPreview";
import type { SubtitleCue } from "@/api/subtitle";

const PROJECT = "00000000-0000-4000-8000-000000000000";

function cue(start: number, end: number, text: string, cueNumber: number): SubtitleCue {
  return {
    id: String(cueNumber),
    project_id: PROJECT,
    cue_number: cueNumber,
    start,
    end,
    text,
    speaker: null,
    source_text: null,
    status: "draft",
    style_json: null,
    updated_at: "t",
  };
}

const CUES = [cue(1, 2, "Chào mừng", 1), cue(3, 4, "Hẹn gặp lại", 2)];

describe("formatTime", () => {
  it("formats m:ss.mmm", () => {
    expect(formatTime(65.5)).toBe("1:05.500");
    expect(formatTime(0)).toBe("0:00.000");
  });
});

describe("VideoPreview (static render)", () => {
  it("mounts the scoped video element with the expected src", () => {
    const html = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/C%3Aclip.mp4" cues={[]} />,
    );
    expect(html).toContain('data-role="video-element"');
    expect(html).toContain('src="media://localhost/C%3Aclip.mp4"');
  });

  it("shows the caption when the playhead is inside a cue", () => {
    const html = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/x" cues={CUES} initialTime={1.5} />,
    );
    expect(html).toContain('data-role="caption"');
    expect(html).toContain("Chào mừng");
    expect(html).toContain('data-cue-number="1"');
  });

  it("overlay style maps the ASS defaults (font-size 44px, bottom margin 24px, stroke)", () => {
    const html = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/x" cues={CUES} initialTime={1.5} />,
    );
    expect(html).toContain("font-size:44px");
    expect(html).toContain("bottom:24px");
    expect(html).toContain("text-shadow:");
    expect(html).toContain("Arial");
  });

  it("hides the caption outside any cue window", () => {
    const html = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/x" cues={CUES} initialTime={4.5} />,
    );
    expect(html).not.toContain('data-role="caption"');
  });

  it("switches cues at the exact end boundary (end exclusive)", () => {
    const atEndOfFirst = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/x" cues={CUES} initialTime={2} />,
    );
    expect(atEndOfFirst).not.toContain("Chào mừng");
    const insideSecond = renderToStaticMarkup(
      <VideoPreview videoUrl="media://localhost/x" cues={CUES} initialTime={3.5} />,
    );
    expect(insideSecond).toContain("Hẹn gặp lại");
  });

  it("renders the transport: play toggle, scrubber and time readout", () => {
    const html = renderToStaticMarkup(<VideoPreview videoUrl="media://localhost/x" cues={[]} />);
    expect(html).toContain('data-role="play-toggle"');
    expect(html).toContain('data-role="scrub"');
    expect(html).toContain('aria-label="Scrub"');
    expect(html).toContain("0:00.000 / 0:00.000");
  });

  it("shows the loading indicator on first render", () => {
    const html = renderToStaticMarkup(<VideoPreview videoUrl="media://localhost/x" cues={[]} />);
    expect(html).toContain('data-role="video-loading"');
  });
});
