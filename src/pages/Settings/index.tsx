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
import { isTauri } from "@/lib/env";
import { useWorker, restartWorker as restartWorkerStore } from "@/stores/worker";
import {
  GeneralSection,
  PrivacySection,
  ProcessingSection,
  ProvidersSection,
  SecuritySection,
  SettingsGroup,
  StorageSection,
  SubtitleSection,
  VideoSection,
  VoiceSection,
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
  const { info: workerInfo } = useWorker();
  const [restarting, setRestarting] = useState(false);
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
      if (!isTauri()) return;
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
    if (!isTauri()) return;
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

  const handleRestartWorker = useCallback(async () => {
    setRestarting(true);
    try {
      await restartWorkerStore();
      toast.push("Worker restarted", "success");
    } catch (error) {
      toast.push(String(error), "error");
    } finally {
      setRestarting(false);
    }
  }, [toast]);

  return (
    <section aria-labelledby="settings-heading" className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 id="settings-heading" className="text-2xl font-semibold tracking-tight">
          Settings
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Configuration applies to new jobs. Sections without backend support are marked clearly.
        </p>
      </div>

      <SettingsGroup id="group-general" title="General" description="App-wide preferences.">
        <GeneralSection />
        <PrivacySection
          settings={settings}
          onSaveMode={(v) => void saveSetting("privacy.mode", v)}
          onSaveTelemetry={(v) => void saveSetting("privacy.telemetry", String(v))}
        />
      </SettingsGroup>

      <SettingsGroup
        id="group-providers"
        title="AI providers"
        description="Translation backends and their credentials."
      >
        <ProvidersSection
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
      </SettingsGroup>

      <SettingsGroup
        id="group-media"
        title="Audio & video"
        description="Defaults for dubbing, video and subtitle output."
      >
        <VoiceSection />
        <VideoSection />
        <SubtitleSection />
      </SettingsGroup>

      <SettingsGroup id="group-storage" title="Storage" description="Cache and model storage.">
        <StorageSection
          quotaBytes={settings["cache.quota_bytes"]}
          onSaveQuota={(v) => void saveSetting("cache.quota_bytes", v)}
        />
      </SettingsGroup>

      <SettingsGroup
        id="group-advanced"
        title="Advanced"
        description="Worker, compute and security — for technical users."
      >
        <ProcessingSection
          settings={settings}
          worker={workerInfo}
          onSaveModel={(v) => void saveSetting("ai.model", v)}
          onSaveDevice={(v) => void saveSetting("ai.device", v)}
          onSavePreset={(v) => void saveSetting("ai.preset", v)}
          onSaveGpu={(v) => void saveSetting("gpu.override", v)}
          onRestartWorker={() => void handleRestartWorker()}
          restarting={restarting}
        />
        <SecuritySection />
        <section
          aria-labelledby="about-subheading"
          className="space-y-3 rounded-lg border border-border bg-card p-4"
        >
          <div>
            <h2 id="about-subheading" className="text-sm font-semibold">
              Connection
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Round-trip to the Rust core (not the AI worker).
            </p>
          </div>
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
      </SettingsGroup>
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
