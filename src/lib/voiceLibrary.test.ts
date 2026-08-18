import { describe, expect, it } from "vitest";

import {
  EMPTY_VOICE_FILTERS,
  filterOptions,
  filterVoices,
  flattenVoices,
  groupVoices,
  languageLabel,
  pushRecent,
  voiceName,
  voiceStatus,
  type VoiceEntry,
} from "./voiceLibrary";
import type { TtsEngineVoices } from "@/api/voices";

const ENGINES: TtsEngineVoices[] = [
  {
    id: "edge",
    label: "Edge (cloud)",
    available: true,
    voices: [
      {
        id: "vi-VN-HoaiMyNeural",
        label: "Vietnamese — female (edge)",
        language: "vi",
        gender: "female",
        age: "Not specified",
        tags: [],
        preview_text: "Xin chào",
      },
      {
        id: "en-US-AriaNeural",
        label: "English (US) — female (edge)",
        language: "en",
        gender: "female",
        age: "Not specified",
        tags: ["Chat", "Narration"],
        preview_text: "Hello",
      },
    ],
  },
  {
    id: "piper",
    label: "Piper (local)",
    available: false,
    voices: [
      {
        id: "vi_VN-vais1000-medium",
        label: "Vietnamese — piper local (medium)",
        language: "vi",
        gender: "Not specified",
        age: "Not specified",
        tags: [],
        preview_text: "Xin chào",
      },
    ],
  },
];

const ALL = flattenVoices(ENGINES);

function entry(id: string): VoiceEntry {
  const found = ALL.find((v) => v.id === id);
  if (!found) throw new Error(`missing fixture voice ${id}`);
  return found;
}

describe("voiceLibrary", () => {
  it("derives honest display names from real voice ids", () => {
    expect(voiceName("vi-VN-HoaiMyNeural")).toBe("HoaiMy");
    expect(voiceName("zh-CN-YunyangNeural")).toBe("Yunyang");
    expect(voiceName("vi_VN-vais1000-medium")).toBe("vi_VN-vais1000-medium");
  });

  it("flattens engines into searchable entries with provider labels", () => {
    expect(ALL).toHaveLength(3);
    expect(entry("vi-VN-HoaiMyNeural").providerLabel).toBe("Free (cloud)");
    expect(entry("vi_VN-vais1000-medium").providerLabel).toBe("Local");
    expect(entry("vi_VN-vais1000-medium").available).toBe(false);
  });

  it("derives filter options from the real data", () => {
    const options = filterOptions(ALL);
    expect(options.languages).toEqual(["en", "vi"]);
    expect(options.genders).toEqual(["female"]);
    expect(options.engines).toEqual(["edge", "piper"]);
  });

  it("searches across name / language / style / gender / provider", () => {
    expect(
      filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, query: "vietnamese" }).map((v) => v.id),
    ).toEqual(["vi-VN-HoaiMyNeural", "vi_VN-vais1000-medium"]);
    // Multi-word AND search.
    expect(
      filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, query: "female english" }).map((v) => v.id),
    ).toEqual(["en-US-AriaNeural"]);
    // Style/tags search.
    expect(
      filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, query: "narrator" }).map((v) => v.id),
    ).toEqual(["en-US-AriaNeural"]);
    // Provider search ("local" → piper).
    expect(filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, query: "local" }).map((v) => v.id)).toEqual([
      "vi_VN-vais1000-medium",
    ]);
  });

  it("filters by language / gender / engine", () => {
    expect(filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, language: "en" }).map((v) => v.id)).toEqual([
      "en-US-AriaNeural",
    ]);
    expect(
      filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, gender: "female" }).map((v) => v.id),
    ).toEqual(["vi-VN-HoaiMyNeural", "en-US-AriaNeural"]);
    expect(filterVoices(ALL, { ...EMPTY_VOICE_FILTERS, engine: "piper" }).map((v) => v.id)).toEqual(
      ["vi_VN-vais1000-medium"],
    );
  });

  it("groups voices into favorites / recent / all without duplicates", () => {
    const groups = groupVoices(ALL, new Set(["vi-VN-HoaiMyNeural"]), ["en-US-AriaNeural"]);
    expect(groups.favorites.map((v) => v.id)).toEqual(["vi-VN-HoaiMyNeural"]);
    expect(groups.recent.map((v) => v.id)).toEqual(["en-US-AriaNeural"]);
    expect(groups.all.map((v) => v.id)).toEqual(["vi_VN-vais1000-medium"]);
  });

  it("pushRecent dedupes and caps at 8", () => {
    expect(pushRecent(["a", "b"], "b")).toEqual(["b", "a"]);
    expect(pushRecent(["1", "2", "3", "4", "5", "6", "7", "8"], "9")).toEqual([
      "9",
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
    ]);
  });

  it("voiceStatus reports engine availability honestly", () => {
    expect(voiceStatus(entry("vi-VN-HoaiMyNeural")).status).toBe("available");
    const local = voiceStatus(entry("vi_VN-vais1000-medium"));
    expect(local.status).toBe("unavailable");
    expect(local.reason).toContain("Engine not installed");
  });

  it("languageLabel falls back to 'Not specified'", () => {
    expect(languageLabel("vi")).toBe("Vietnamese");
    expect(languageLabel("")).toBe("Not specified");
    expect(languageLabel("xx")).toBe("xx");
  });
});
