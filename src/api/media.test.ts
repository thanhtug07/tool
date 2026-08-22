import { describe, expect, it } from "vitest";

import { toMediaUrl } from "./media";

describe("toMediaUrl", () => {
  it("returns the raw path or URL in web mode", () => {
    expect(toMediaUrl("C:\\Videos\\clip.mp4")).toBe("C:\\Videos\\clip.mp4");
  });

  it("handles empty or whitespace path", () => {
    expect(toMediaUrl("")).toBe("");
    expect(toMediaUrl("   ")).toBe("");
  });
});
