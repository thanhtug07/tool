import { useCallback, useEffect, useState } from "react";

import { ping, type PingError, type PingResult } from "@/api/bridge";
import {
  deleteApiKey,
  getApiKeyMasked,
  getSettings,
  setApiKey,
  setSetting,
  type ApiProvider,
  type SettingsKey,
  type SettingsSnapshot,
} from "@/api/settings";
import { useToast } from "@/components/toast";
import { Button } from "@/components/ui/button";
import {
  AiSettingsSection,
  ApiSettingsSection,
  CacheSettingsSection,
  GpuSettingsSection,
  PrivacySettingsSection,
} from "./sections";

type ConnectionState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "success"; result: PingResult }
  | { status: "error"; error: PingError };

export type { ConnectionState };

/** Fallback snapshot so the page renders before settings load (never stored). */
const FALLBACK_SETTINGS: SettingsSnapshot = {
  "ai.model": "large-v3",
  "ai.device": "auto",
  "ai.preset": "balanced",
  "gpu.override": "auto",
  "api.gemini.base_url": "",
  "api.gemini.model": "gemini-2.5-flash-lite",
  "api.local.base_url": "http://127.0.0.1:8080",
  "cache.quota_bytes": 10737418240,
  "privacy.mode": "local",
  "privacy.telemetry": false,
};

export default function SettingsPage() {
  const toast = useToast();
  const [connection, setConnection] = useState<ConnectionState>({ status: "idle" });
  const [settings, setSettings] = useState<SettingsSnapshot>(FALLBACK_SETTINGS);
  const [provider, setProvider] = useState<ApiProvider>("gemini");
  const [maskedKey, setMaskedKey] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [savingKey, setSavingKey] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await getSettings();
        if (!cancelled) {
          setSettings(snapshot);
          setBaseUrl(snapshot["api.gemini.base_url"]);
        }
      } catch (error) {
        if (!cancelled) {
          toast.push(String(error), "error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    void getApiKeyMasked(provider)
      .then((masked) => {
        if (!cancelled) {
          setMaskedKey(masked);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          toast.push(String(error), "error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [provider, toast]);

  const saveSetting = useCallback(
    async (key: SettingsKey, value: string) => {
      try {
        const updated = await setSetting(key, value);
        setSettings(updated);
        toast.push(`Saved ${key}`, "success");
      } catch (error) {
        toast.push(String(error), "error");
      }
    },
    [toast],
  );

  const handleSaveKey = useCallback(async () => {
    setSavingKey(true);
    try {
      await setApiKey(provider, keyDraft.trim());
      const masked = await getApiKeyMasked(provider);
      setMaskedKey(masked);
      setKeyDraft("");
      toast.push("API key saved to Windows Credential Manager", "success");
    } catch (error) {
      toast.push(String(error), "error");
    } finally {
      setSavingKey(false);
    }
  }, [provider, keyDraft, toast]);

  const handleDeleteKey = useCallback(async () => {
    try {
      await deleteApiKey(provider);
      setMaskedKey(null);
      toast.push("API key removed", "success");
    } catch (error) {
      toast.push(String(error), "error");
    }
  }, [provider, toast]);

  const handleTestConnection = useCallback(async () => {
    setConnection({ status: "testing" });
    try {
      const result = await ping();
      setConnection({ status: "success", result });
    } catch (error) {
      setConnection({ status: "error", error: error as PingError });
    }
  }, []);

  return (
    <section aria-labelledby="settings-heading" className="space-y-6">
      <h1 id="settings-heading" className="text-lg font-semibold">
        Settings
      </h1>
      <p className="text-sm text-muted-foreground">
        Configuration applies to new jobs. API keys are stored in the Windows Credential Manager —
        never in the database.
      </p>

      <div className="space-y-6">
        <AiSettingsSection settings={settings} onSave={saveSetting} />
        <GpuSettingsSection
          settings={settings}
          onSave={(v) => void saveSetting("gpu.override", v)}
        />
        <ApiSettingsSection
          provider={provider}
          maskedKey={maskedKey}
          baseUrl={baseUrl}
          keyDraft={keyDraft}
          saving={savingKey}
          onProviderChange={(next) => {
            setProvider(next);
            setBaseUrl(
              next === "local" ? settings["api.local.base_url"] : settings["api.gemini.base_url"],
            );
            setKeyDraft("");
          }}
          onBaseUrlChange={setBaseUrl}
          onSaveBaseUrl={() =>
            void saveSetting(
              provider === "local" ? "api.local.base_url" : "api.gemini.base_url",
              baseUrl,
            )
          }
          onKeyDraftChange={setKeyDraft}
          onSaveKey={() => void handleSaveKey()}
          onDeleteKey={() => void handleDeleteKey()}
        />
        <CacheSettingsSection
          quotaBytes={settings["cache.quota_bytes"]}
          onSave={(v) => void saveSetting("cache.quota_bytes", v)}
        />
        <PrivacySettingsSection
          settings={settings}
          onSaveMode={(v) => void saveSetting("privacy.mode", v)}
          onSaveTelemetry={(v) => void saveSetting("privacy.telemetry", String(v))}
        />

        <section aria-labelledby="about-subheading" className="space-y-2">
          <h2 id="about-subheading" className="text-sm font-medium">
            About
          </h2>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              onClick={handleTestConnection}
              disabled={connection.status === "testing"}
            >
              Test connection
            </Button>
            <ConnectionStatus state={connection} />
          </div>
        </section>
      </div>
    </section>
  );
}

export function ConnectionStatus({ state }: { state: ConnectionState }) {
  if (state.status === "idle") {
    return <p className="text-sm text-muted-foreground">Not tested yet.</p>;
  }

  if (state.status === "testing") {
    return <p className="text-sm text-muted-foreground">Testing connection…</p>;
  }

  if (state.status === "success") {
    return (
      <p className="text-sm text-emerald-400">
        {state.result.response} ({state.result.latencyMs} ms)
      </p>
    );
  }

  return <p className="text-sm text-destructive">{state.error.message}</p>;
}
