import { safeInvoke } from "@/api/invoke";

/** Whitelisted settings keys exposed to the UI (mirrors Rust SettingsService). */
export type SettingsKey =
  | "ai.model"
  | "ai.device"
  | "ai.preset"
  | "gpu.override"
  | "api.gemini.base_url"
  | "api.gemini.model"
  | "api.local.base_url"
  | "cache.quota_bytes"
  | "privacy.mode"
  | "privacy.telemetry"
  | "tts.engine"
  | "tts.voice"
  | "automation.chunked"
  | "automation.chunk_duration"
  | "automation.chunk_overlap"
  | "automation.chunk_concurrency"
  | "automation.chunk_retries"
  | "automation.stt_mode"
  | "automation.stt_batch_size"
  | "automation.orchestrator_v2";

/** Typed snapshot of every known setting (stored value or built-in default). */
export type SettingsSnapshot = {
  "ai.model": string;
  "ai.device": "auto" | "cuda" | "cpu";
  "ai.preset": string;
  "gpu.override": "auto" | "cuda" | "cpu";
  "api.gemini.base_url": string;
  "api.gemini.model": string;
  "api.local.base_url": string;
  "cache.quota_bytes": number;
  "privacy.mode": "local" | "cloud";
  "privacy.telemetry": boolean;
  "tts.engine": "edge" | "piper";
  "tts.voice": string;
  "automation.orchestrator_v2": boolean;
};

/** Providers that can hold an API key in the OS credential vault. */
export type ApiProvider = "gemini" | "local" | "openai";

/** Store an API key for `provider` in the OS credential vault. */
export function setApiKey(provider: ApiProvider, key: string): Promise<void> {
  return safeInvoke("secrets.set_api_key", { provider, key });
}

/**
 * Masked form of the stored key (e.g. `AIz****wxyz`) or `null` when none is
 * stored. The full secret never leaves the Rust core.
 */
export function getApiKeyMasked(provider: ApiProvider): Promise<string | null> {
  return safeInvoke("secrets.get_api_key_masked", { provider });
}

/** Remove the stored key for `provider`. */
export function deleteApiKey(provider: ApiProvider): Promise<void> {
  return safeInvoke("secrets.delete_api_key", { provider });
}

/** Read every known setting (stored value or default). */
export function getSettings(): Promise<SettingsSnapshot> {
  return safeInvoke("settings.get_all");
}

/** Validate + persist one setting; resolves with the updated snapshot. */
export function setSetting(key: SettingsKey, value: string): Promise<SettingsSnapshot> {
  return safeInvoke("settings.set", { key, value });
}
