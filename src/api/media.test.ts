import { describe, expect, it } from "vitest";

import { toMediaUrl } from "./media";

describe("toMediaUrl", () => {
  it("wraps a Windows path into the scoped media scheme", () => {
    expect(toMediaUrl("C:\\Videos\\clip.mp4")).toBe("media://localhost/C%3A%5CVideos%5Cclip.mp4");
  });

  it("percent-encodes unicode and spaces", () => {
    const url = toMediaUrl("D:\\Phim Việt\\đoạn phim.mp4");
    const encoded = url.replace("media://localhost/", "");
    expect(encoded).not.toContain(" ");
    expect(decodeURIComponent(encoded)).toBe("D:\\Phim Việt\\đoạn phim.mp4");
  });

  it("handles an empty path (no media loaded)", () => {
    expect(toMediaUrl("")).toBe("media://localhost/");
  });
});
