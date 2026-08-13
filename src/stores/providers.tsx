import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { listProviders, type ProviderCapability, type ProviderView } from "@/api/provider";
import { isTauri } from "@/lib/env";

export type ProvidersContextValue = {
  /** Every registered provider (enabled + disabled), FREE first. */
  providers: ProviderView[];
  /** capability → provider id (seeded: translation/stt/tts → free). */
  defaults: Record<string, string>;
  /** Providers that can serve `capability` and are enabled. */
  providersFor: (capability: ProviderCapability) => ProviderView[];
  /** The enabled default for `capability` (falls back to free). */
  defaultFor: (capability: string) => ProviderView | null;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

const ProvidersContext = createContext<ProvidersContextValue | null>(null);

/** Fallback registry for non-Tauri contexts (browser preview / tests): mirrors
 * the seeded builtins so the UI renders without the Rust core. */
const FALLBACK_PROVIDERS: ProviderView[] = [
  {
    id: "free",
    name: "FREE",
    provider_type: "translation",
    provider_kind: "free",
    enabled: true,
    base_url: "http://127.0.0.1:8080",
    model: null,
    config: {},
    capabilities: ["translation", "stt"],
    last_test_status: null,
    last_test_at: null,
    created_at: "",
    updated_at: "",
    needs_key: false,
    api_key_configured: false,
  },
  {
    id: "gemini",
    name: "Gemini (cloud)",
    provider_type: "translation",
    provider_kind: "gemini",
    enabled: true,
    base_url: null,
    model: "gemini-2.5-flash-lite",
    config: {},
    capabilities: ["translation"],
    last_test_status: null,
    last_test_at: null,
    created_at: "",
    updated_at: "",
    needs_key: true,
    api_key_configured: false,
  },
  {
    id: "local",
    name: "Local LLM",
    provider_type: "translation",
    provider_kind: "local",
    enabled: true,
    base_url: "http://127.0.0.1:8080",
    model: null,
    config: {},
    capabilities: ["translation"],
    last_test_status: null,
    last_test_at: null,
    created_at: "",
    updated_at: "",
    needs_key: false,
    api_key_configured: false,
  },
  {
    id: "mock",
    name: "Mock (offline)",
    provider_type: "translation",
    provider_kind: "mock",
    enabled: true,
    base_url: null,
    model: null,
    config: {},
    capabilities: ["translation"],
    last_test_status: null,
    last_test_at: null,
    created_at: "",
    updated_at: "",
    needs_key: false,
    api_key_configured: false,
  },
];

export function ProvidersProvider({ children }: { children: ReactNode }) {
  const [providers, setProviders] = useState<ProviderView[]>(FALLBACK_PROVIDERS);
  const [defaults, setDefaults] = useState<Record<string, string>>({
    translation: "free",
    stt: "free",
    tts: "free",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const data = await listProviders();
      setProviders(data.providers);
      setDefaults(data.defaults);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<ProvidersContextValue>(() => {
    const providersFor = (capability: ProviderCapability) =>
      providers.filter((p) => p.enabled && p.capabilities.includes(capability));
    const defaultFor = (capability: string) => {
      const id = defaults[capability] ?? "free";
      return providers.find((p) => p.id === id) ?? null;
    };
    return {
      providers,
      defaults,
      providersFor,
      defaultFor,
      loaded: !loading,
      loading,
      error,
      refresh,
    };
  }, [providers, defaults, loading, error, refresh]);

  return <ProvidersContext.Provider value={value}>{children}</ProvidersContext.Provider>;
}

export function useProviders(): ProvidersContextValue {
  const ctx = useContext(ProvidersContext);
  if (!ctx) {
    throw new Error("useProviders must be used inside <ProvidersProvider>");
  }
  return ctx;
}
