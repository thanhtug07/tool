import { useCallback, useEffect, useState } from "react";
import {
  Captions,
  Clapperboard,
  Database,
  Info,
  LayoutGrid,
  Settings as SettingsIcon,
  Sparkles,
  Cpu,
  type LucideIcon,
} from "lucide-react";

import { ping, type PingError, type PingResult } from "@/api/bridge";
import { getSettings, setSetting, type SettingsKey, type SettingsSnapshot } from "@/api/settings";
import { useToast } from "@/components/toast";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { isTauri } from "@/lib/env";
import { useWorker, restartWorker as restartWorkerStore } from "@/stores/worker";
import {
  GeneralSection,
  PrivacySection,
  ProcessingSection,
  SecuritySection,
  SettingsGroup,
  StorageSection,
  SubtitleSection,
  VideoSection,
  VoiceSection,
} from "./sections";
import { ProvidersPanel } from "./ProvidersPanel";

type ConnectionState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "success"; result: PingResult }
  | { status: "error"; error: PingError };

export type { ConnectionState };

const FALLBACK_SETTINGS: SettingsSnapshot = {
  "ai.model": "large-v3",
  "ai.device": "auto",
  "ai.preset": "balanced",
  "gpu.override": "auto",
  "api.gemini.base_url": "",
  "api.gemini.model": "gemini-flash-lite-latest",
  "api.local.base_url": "http://127.0.0.1:8080",
  "cache.quota_bytes": 10737418240,
  "privacy.mode": "local",
  "privacy.telemetry": false,
  "tts.engine": "edge",
  "tts.voice": "vi-VN-HoaiMyNeural",
};

type SettingsNavKey = "general" | "providers" | "ai" | "subtitle" | "video" | "storage" | "about";

const SETTINGS_NAV: { key: SettingsNavKey; label: string; icon: LucideIcon }[] = [
  { key: "general", label: "General", icon: LayoutGrid },
  { key: "providers", label: "Providers", icon: Sparkles },
  { key: "ai", label: "AI", icon: Cpu },
  { key: "video", label: "Video", icon: Clapperboard },
  { key: "subtitle", label: "Subtitle", icon: Captions },
  { key: "storage", label: "Storage", icon: Database },
  { key: "about", label: "About", icon: Info },
];

export default function SettingsPage({
  initialSection = "general",
}: {
  initialSection?: SettingsNavKey | "voice" | "processing";
}) {
  // Map legacy section keys (voice/processing) onto the merged AI group.
  const resolvedInitial: SettingsNavKey =
    initialSection === "voice" || initialSection === "processing" ? "ai" : initialSection;
  const toast = useToast();
  const { info: workerInfo } = useWorker();
  const [active, setActive] = useState<SettingsNavKey>(resolvedInitial);
  const [restarting, setRestarting] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>({ status: "idle" });
  const [settings, setSettings] = useState<SettingsSnapshot>(FALLBACK_SETTINGS);
  const [showAdvancedAi, setShowAdvancedAi] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!isTauri()) return;
      try {
        const snapshot = await getSettings();
        if (!cancelled) setSettings(snapshot);
      } catch (error) {
        if (!cancelled) toast.push(String(error), "error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toast]);

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
    <section aria-labelledby="settings-heading" className="mx-auto flex max-w-5xl gap-4">
      <nav aria-label="Settings" className="w-40 shrink-0 space-y-0.5">
        <div className="mb-2 flex items-center gap-2">
          <SettingsIcon className="size-4 text-muted-foreground" aria-hidden="true" />
          <h1 id="settings-heading" className="text-sm font-semibold tracking-tight">
            Settings
          </h1>
        </div>
        {SETTINGS_NAV.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            data-role={`settings-nav-${key}`}
            onClick={() => setActive(key)}
            className={cn(
              "flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-xs transition-colors",
              active === key
                ? "bg-accent font-medium text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )}
          >
            <Icon className="size-3.5 shrink-0" aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1 space-y-4">
        {active === "general" && (
          <SettingsGroup id="group-general" title="General" description="App-wide preferences.">
            <GeneralSection />
            <PrivacySection
              settings={settings}
              onSaveMode={(v) => void saveSetting("privacy.mode", v)}
              onSaveTelemetry={(v) => void saveSetting("privacy.telemetry", String(v))}
            />
          </SettingsGroup>
        )}
        {active === "providers" && (
          <SettingsGroup
            id="group-providers"
            title="Providers"
            description="API keys live in the OS credential vault (Windows Credential Manager)."
          >
            <ProvidersPanel />
          </SettingsGroup>
        )}
        {active === "ai" && (
          <SettingsGroup id="group-ai" title="AI" description="Voice defaults and compute.">
            <VoiceSection
              settings={settings}
              onSaveEngine={(v) => void saveSetting("tts.engine", v)}
              onSaveVoice={(v) => void saveSetting("tts.voice", v)}
            />
            <button
              type="button"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={() => setShowAdvancedAi((v) => !v)}
            >
              {showAdvancedAi ? "Hide advanced" : "Advanced"}
            </button>
            {showAdvancedAi && (
              <>
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
              </>
            )}
          </SettingsGroup>
        )}
        {active === "subtitle" && (
          <SettingsGroup
            id="group-subtitle"
            title="Subtitle"
            description="Burned-in caption defaults."
          >
            <SubtitleSection />
          </SettingsGroup>
        )}
        {active === "video" && (
          <SettingsGroup id="group-video" title="Video" description="Output defaults.">
            <VideoSection />
          </SettingsGroup>
        )}
        {active === "storage" && (
          <SettingsGroup id="group-storage" title="Storage" description="Cache and model storage.">
            <StorageSection
              quotaBytes={settings["cache.quota_bytes"]}
              onSaveQuota={(v) => void saveSetting("cache.quota_bytes", v)}
            />
          </SettingsGroup>
        )}
        {active === "about" && (
          <SettingsGroup id="group-about" title="About" description="Connection and app info.">
            <section className="space-y-3 rounded-lg border border-border bg-card p-4">
              <div>
                <h2 className="text-sm font-semibold">Connection</h2>
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
        )}
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
