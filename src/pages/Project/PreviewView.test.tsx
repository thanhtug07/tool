import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/api/subtitle", () => ({
  getSubtitleCues: vi.fn(),
  updateSubtitleCue: vi.fn(),
  replaceSubtitleCues: vi.fn(),
}));

import PreviewView from "./PreviewView";

describe("PreviewView (unit — mocked bridge, static render)", () => {
  it("renders the preview shell with project and video inputs", () => {
    const html = renderToStaticMarkup(<PreviewView />);
    expect(html).toContain("Preview");
    expect(html).toContain("Project ID");
    expect(html).toContain("Video path");
    expect(html).toContain("Load");
    expect(html).toContain('data-role="preview-empty"');
  });

  it("does not mount the player until a video is loaded", () => {
    const html = renderToStaticMarkup(<PreviewView />);
    expect(html).not.toContain('data-role="video-element"');
  });
});
