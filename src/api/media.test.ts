import { describe, expect, it } from "vitest";

import { toMediaUrl } from "./media";

describe("toMediaUrl", () => {
  it("converts OS paths to /api/media/stream URLs", () => {
    // Windows path — use String.raw to avoid JS escape interpretation
    expect(toMediaUrl(String.raw`C:\Videos\clip.mp4`)).toBe(
      "http://127.0.0.1:8765/api/media/stream?path=C%3A%5CVideos%5Cclip.mp4",
    );
    expect(toMediaUrl("/home/user/video.mp4")).toBe(
      "http://127.0.0.1:8765/api/media/stream?path=%2Fhome%2Fuser%2Fvideo.mp4",
    );
  });

  it("passes through HTTP URLs unchanged", () => {
    expect(toMediaUrl("https://example.com/video.mp4")).toBe(
      "https://example.com/video.mp4",
    );
    expect(toMediaUrl("http://localhost:8080/stream.mp4")).toBe(
      "http://localhost:8080/stream.mp4",
    );
  });

  it("handles empty or whitespace path", () => {
    expect(toMediaUrl("")).toBe("");
    expect(toMediaUrl("   ")).toBe("");
  });

  it("handles null/undefined gracefully", () => {
    // @ts-expect-error testing null input
    expect(toMediaUrl(null)).toBe("");
    // @ts-expect-error testing undefined input
    expect(toMediaUrl(undefined)).toBe("");
  });
});
