import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadStudioOptions, saveStudioOptions, type StudioSessionOptions } from "./session";

function makeStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => void store.delete(key),
    setItem: (key: string, value: string) => void store.set(key, value),
  };
}

const BASE_OPTIONS = {
  sourceLanguage: "en",
  targetLanguage: "vi",
  provider: "free",
  burnSubtitles: true,
  dubAudio: true,
  voice: "vi-VN-HoaiMyNeural",
  ttsEngine: "edge",
  watermark: {
    kind: "none",
    text: "",
    imagePath: "",
    position: "bottom-right",
    margin: 24,
    x: 0,
    y: 0,
    fontSize: 48,
    color: "#FFFFFFFF",
    opacity: 1,
    rotation: 0,
    font: "",
    imageWidth: 0,
  },
} satisfies StudioSessionOptions;

describe("studio session (localStorage stub)", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", makeStorage());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("round-trips options including outputFolder", () => {
    const options: StudioSessionOptions = {
      ...BASE_OPTIONS,
      chunked: true,
      outputFolder: "C:\\Users\\me\\Videos\\final",
    };
    saveStudioOptions("p1", options);
    expect(loadStudioOptions("p1")).toEqual(options);
  });

  it("loads a persisted session that predates outputFolder (migration)", () => {
    const legacy = { ...BASE_OPTIONS, chunked: false };
    localStorage.setItem("studio.options.p1", JSON.stringify(legacy));
    const restored = loadStudioOptions("p1");
    expect(restored).toEqual(legacy);
    expect(restored?.outputFolder).toBeUndefined();
  });

  it("rejects a persisted session with a non-string outputFolder", () => {
    const invalid = { ...BASE_OPTIONS, outputFolder: 42 };
    localStorage.setItem("studio.options.p1", JSON.stringify(invalid));
    expect(loadStudioOptions("p1")).toBeNull();
  });
});
