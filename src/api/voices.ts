import { safeInvoke } from "@/api/invoke";

/**
 * One selectable TTS voice with Voice Library metadata. The worker is the
 * source of truth — fields the provider does not expose arrive as
 * "Not specified" / "" (never invented on the frontend).
 */
export type TtsVoice = {
  id: string;
  label: string;
  language: string;
  gender: string;
  age: string;
  tags: string[];
  preview_text: string;
};

/** One TTS engine (edge / piper) with its voices + installed status. */
export type TtsEngineVoices = {
  id: string;
  label: string;
  available: boolean;
  voices: TtsVoice[];
};

/** `settings.voices` payload — engines + per-engine default voices. */
export type TtsVoicesResult = {
  engines: TtsEngineVoices[];
  defaults: Record<string, { voice: string }>;
};

/** `settings.ttsPreview` payload — a real synthesized clip path. */
export type TtsPreviewResult = {
  path: string;
  duration_seconds: number;
  cached: boolean;
};

export function getTtsVoices(): Promise<TtsVoicesResult> {
  return safeInvoke("settings.voices");
}

/** Synthesize one short preview clip (worker-cached by engine+voice+text). */
export function ttsPreview(engine: string, voice: string, text: string): Promise<TtsPreviewResult> {
  return safeInvoke("settings.ttsPreview", { engine, voice, text });
}
