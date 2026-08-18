import type { TtsEngineVoices, TtsVoice } from "@/api/voices";

/**
 * VOICE LIBRARY — pure helpers over the worker voice registry.
 *
 * Everything here is derived from REAL voice data (engine + id + label +
 * metadata). Names are extracted from the voice id (Microsoft neural voice
 * ids literally carry the name, e.g. `vi-VN-HoaiMyNeural` → "HoaiMy"); gender
 * / age / tags come from the provider catalogue or "Not specified".
 */

/** A voice flattened with its engine + provider, ready for the UI. */
export type VoiceEntry = TtsVoice & {
  engine: string;
  engineLabel: string;
  providerLabel: string;
  available: boolean;
  /** Display name derived from the real voice id. */
  name: string;
};

/** Engine id → provider label (engines are the TTS providers here). */
const ENGINE_PROVIDER: Record<string, string> = {
  edge: "Free (cloud)",
  piper: "Local",
};

const LANGUAGE_LABELS: Record<string, string> = {
  vi: "Vietnamese",
  en: "English",
  zh: "Chinese",
  ja: "Japanese",
  ko: "Korean",
  fr: "French",
  de: "German",
  es: "Spanish",
};

export function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] ?? (code || "Not specified");
}

/** Human name from a Microsoft neural voice id (`vi-VN-HoaiMyNeural` → "HoaiMy"). */
export function voiceName(id: string): string {
  const region = id.match(/^[a-z]{2}-[A-Z]{2}-/);
  const base = region ? id.slice(region[0].length) : id;
  return base.replace(/Neural$/, "") || id;
}

/** Flatten the worker engines into one searchable voice list. */
export function flattenVoices(engines: TtsEngineVoices[]): VoiceEntry[] {
  const entries: VoiceEntry[] = [];
  for (const engine of engines) {
    for (const voice of engine.voices) {
      entries.push({
        ...voice,
        engine: engine.id,
        engineLabel: engine.label,
        providerLabel: ENGINE_PROVIDER[engine.id] ?? engine.label,
        available: engine.available,
        name: voiceName(voice.id),
      });
    }
  }
  return entries;
}

export type VoiceFilters = {
  query: string;
  language: string;
  gender: string;
  engine: string;
};

export const EMPTY_VOICE_FILTERS: VoiceFilters = {
  query: "",
  language: "all",
  gender: "all",
  engine: "all",
};

/** Distinct filter option values derived from the real data (never hard-coded). */
export function filterOptions(voices: VoiceEntry[]) {
  return {
    languages: [...new Set(voices.map((v) => v.language).filter(Boolean))].sort(),
    genders: [...new Set(voices.map((v) => v.gender).filter((g) => g !== "Not specified"))].sort(),
    engines: [...new Set(voices.map((v) => v.engine))].sort(),
  };
}

/** Style aliases so natural search words find the provider's documented tags
 * (e.g. "narrator" finds a voice tagged "Narration"). Aliases, never data. */
const TAG_ALIASES: Record<string, string[]> = {
  Narration: ["Narrator", "Narrating"],
  Narrator: ["Narration", "Narrating"],
  News: ["Newscast"],
  Chat: ["Conversation", "Conversational"],
};

/** Search across name / language / provider / style / gender / tags. */
export function filterVoices(voices: VoiceEntry[], filters: VoiceFilters): VoiceEntry[] {
  const q = filters.query.trim().toLowerCase();
  return voices.filter((voice) => {
    if (filters.language !== "all" && voice.language !== filters.language) return false;
    if (filters.gender !== "all" && voice.gender !== filters.gender) return false;
    if (filters.engine !== "all" && voice.engine !== filters.engine) return false;
    if (!q) return true;
    const styleWords = voice.tags.flatMap((tag) => [tag, ...(TAG_ALIASES[tag] ?? [])]);
    const haystack = [
      voice.name,
      voice.label,
      languageLabel(voice.language),
      voice.gender,
      voice.age,
      voice.providerLabel,
      voice.engine,
      ...styleWords,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return q.split(/\s+/).every((word) => haystack.includes(word));
  });
}

/** Section grouping: favorites first, then recently used, then everything. */
export function groupVoices(
  filtered: VoiceEntry[],
  favorites: Set<string>,
  recent: string[],
): { favorites: VoiceEntry[]; recent: VoiceEntry[]; all: VoiceEntry[] } {
  const byId = new Map(filtered.map((v) => [v.id, v]));
  const favoritesSection = filtered.filter((v) => favorites.has(v.id));
  const recentSection = recent.map((id) => byId.get(id)).filter((v): v is VoiceEntry => Boolean(v));
  // Never repeat a favorite/recent card in the "All" list.
  const shown = new Set([...favoritesSection, ...recentSection].map((v) => v.id));
  return {
    favorites: favoritesSection,
    recent: recentSection,
    all: filtered.filter((v) => !shown.has(v.id)),
  };
}

/** Push a voice into the recently-used list (dedupe, cap at 8). */
export function pushRecent(voices: string[], voiceId: string, max = 8): string[] {
  return [voiceId, ...voices.filter((id) => id !== voiceId)].slice(0, max);
}

/** Availability reason — honest, from real data. */
export function voiceStatus(voice: VoiceEntry): {
  status: "available" | "unavailable";
  reason: string;
} {
  if (voice.available) return { status: "available", reason: "Available" };
  return {
    status: "unavailable",
    reason: "Engine not installed — run `pip install edge-tts` (edge) or `piper-tts` (piper).",
  };
}
