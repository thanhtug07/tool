import { safeInvoke } from "@/api/invoke";

/** Capability identifiers (mirrors Rust ProviderService). */
export type ProviderCapability = "translation" | "stt" | "tts";

/** Worker registry kinds (mirrors worker build_translation_provider). */
export type ProviderKind = "free" | "gemini" | "local" | "mock";

/** One provider row as the Rust core exposes it (never contains a secret). */
export type ProviderView = {
  id: string;
  name: string;
  provider_type: string;
  provider_kind: ProviderKind;
  enabled: boolean;
  base_url: string | null;
  model: string | null;
  config: Record<string, unknown>;
  capabilities: ProviderCapability[];
  last_test_status: "success" | "failure" | null;
  last_test_at: string | null;
  created_at: string;
  updated_at: string;
  /** Whether the worker kind needs an API key. */
  needs_key: boolean;
  /** Whether a key is stored in the OS credential vault. */
  api_key_configured: boolean;
};

/** `providers.list` payload: rows + capability-level defaults. */
export type ProvidersList = {
  providers: ProviderView[];
  defaults: Record<string, string>;
};

/** Create/update payload. `apiKey` is stored only on success (Save & Test). */
export type ProviderInput = {
  name: string;
  provider_type: string;
  provider_kind: ProviderKind;
  capabilities: ProviderCapability[];
  base_url?: string | null;
  model?: string | null;
  config?: Record<string, unknown> | null;
  api_key?: string | null;
  clear_key?: boolean;
  enabled?: boolean | null;
};

export type ProviderTestResult = {
  ok: boolean;
  latency_ms: number;
  detail: string;
};

export function listProviders(): Promise<ProvidersList> {
  return safeInvoke("providers.list");
}

export function getProvider(id: string): Promise<ProviderView> {
  return safeInvoke("providers.get", { id });
}

export function createProvider(input: ProviderInput, test?: boolean): Promise<ProviderView> {
  return safeInvoke("providers.create", { input, test });
}

export function updateProvider(
  id: string,
  input: ProviderInput,
  test?: boolean,
): Promise<ProviderView> {
  return safeInvoke("providers.update", { id, input, test });
}

export function deleteProvider(id: string): Promise<void> {
  return safeInvoke("providers.delete", { id });
}

export function setProviderDefault(id: string, capability: string): Promise<void> {
  return safeInvoke("providers.set_default", { id, capability });
}

export function setProviderEnabled(id: string, enabled: boolean): Promise<ProviderView> {
  return safeInvoke("providers.set_enabled", { id, enabled });
}

/** Test with the stored key, or `apiKey` as a one-shot override (never stored). */
export function testProvider(id: string, apiKey?: string | null): Promise<ProviderTestResult> {
  return safeInvoke("providers.test", { id, apiKey });
}
