import { afterEach, describe, expect, it, vi } from "vitest";

import { toMediaUrl } from "./media";

function setTauriShell() {
  (globalThis as Record<string, unknown>).window = {
    __TAURI_INTERNALS__: {
      convertFileSrc: (p: string) => `asset://localhost/${encodeURIComponent(p)}`,
    },
  };
}

function clearTauriShell() {
  delete (globalThis as Record<string, unknown>).window;
}

describe("toMediaUrl", () => {
  afterEach(() => {
    clearTauriShell();
    vi.restoreAllMocks();
  });

  it("uses the Tauri asset protocol inside the shell", () => {
    setTauriShell();
    expect(toMediaUrl("C:\\Videos\\clip.mp4")).toBe("asset://localhost/C%3A%5CVideos%5Cclip.mp4");
  });

  it("percent-encodes unicode and spaces inside the shell", () => {
    setTauriShell();
    const url = toMediaUrl("D:\\Phim Việt\\đoạn phim.mp4");
    expect(url.startsWith("asset://localhost/")).toBe(true);
    expect(url).not.toContain(" ");
    expect(decodeURIComponent(url.replace("asset://localhost/", ""))).toBe(
      "D:\\Phim Việt\\đoạn phim.mp4",
    );
  });

  it("returns the raw path outside the Tauri shell (browser preview)", () => {
    clearTauriShell();
    expect(toMediaUrl("C:\\Videos\\clip.mp4")).toBe("C:\\Videos\\clip.mp4");
  });

  it("handles an empty/whitespace path (no media loaded)", () => {
    setTauriShell();
    expect(toMediaUrl("")).toBe("");
    expect(toMediaUrl("   ")).toBe("");
  });
});
