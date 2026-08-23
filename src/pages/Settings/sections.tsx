import { useEffect, useState, type ReactNode } from "react";

import type { SettingsSnapshot } from "@/api/settings";
import type { WorkerStateInfo } from "@/api/worker";
import { workerStateLabel } from "@/lib/nav";
import { getTtsVoices, type TtsEngineVoices } from "@/api/voices";
import VoicePickerButton from "@/components/voices/VoicePickerButton";

/** Top-level group shell (General / AI providers / Audio & video / Storage / Advanced). */
export function SettingsGroup({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <div>
        <h2 id={id} className="text-base font-semibold tracking-tight">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

/** Section shell with a title and optional description. */
export function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="space-y-3 rounded-lg border border-border bg-card p-4">
      <div>
        <h2 id={id} className="text-sm font-semibold">
          {title}
        </h2>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

/** Read-only label/value row (honest info — no fake controls). */
export function InfoRow({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="truncate text-right" title={title ?? value}>
        {value}
      </span>
    </div>
  );
}

/** Row with a label + a real control. */
export function ControlRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-44 shrink-0 text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

/** Disabled "Coming soon" block — the backend has no such capability yet. */
export function ComingSoon({ label, note }: { label: string; note: string }) {
  return (
    <div className="space-y-1 rounded-md border border-dashed border-border bg-muted/20 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Coming soon
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{note}</p>
    </div>
  );
}

// ---- GENERAL ---------------------------------------------------------------

export function GeneralSection() {
  return (
    <SettingsSection id="general-subheading" title="General">
      <InfoRow label="Interface language" value="English (fixed in this build)" />
      <InfoRow label="Theme" value="Dark (default)" />
      <InfoRow label="Startup behavior" value="Open on Dashboard" />
    </SettingsSection>
  );
}

// ---- VOICE / SUBTITLE / VIDEO (backend-fixed) ------------------------------

const TTS_ENGINES: Record<string, { label: string }> = {
  edge: { label: "Edge (cloud — Microsoft neural, best quality)" },
  piper: { label: "Piper (local — offline, lower quality)" },
};

export function VoiceSection({
  settings,
  onSaveEngine,
  onSaveVoice,
}: {
  settings: SettingsSnapshot;
  onSaveEngine: (value: string) => void;
  onSaveVoice: (value: string) => void;
}) {
  const [engines, setEngines] = useState<TtsEngineVoices[]>([]);
  const [engineError, setEngineError] = useState<string | null>(null);

  const engine = settings["tts.engine"];
  const voice = settings["tts.voice"];

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await getTtsVoices();
        if (!cancelled) setEngines(result.engines);
      } catch (error) {
        if (!cancelled) setEngineError(String(error));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const engineVoices = engines.find((e) => e.id === engine)?.voices ?? [];
  const voiceLabel =
    engineVoices.find((v) => v.id === voice)?.label ?? TTS_ENGINES[engine]?.label ?? "edge";

  return (
    <SettingsSection
      id="voice-subheading"
      title="Voice"
      description="Default dubbing voice applied to new Automation runs."
    >
      <ControlRow label="Engine">
        <select
          data-role="tts-engine"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={engine}
          disabled={engine !== "edge" && engine !== "piper"}
          onChange={(event) => onSaveEngine(event.target.value)}
        >
          <option value="edge">{TTS_ENGINES["edge"].label}</option>
          <option value="piper">{TTS_ENGINES["piper"].label}</option>
        </select>
      </ControlRow>
      <ControlRow label="Voice">
        <div className="w-full max-w-xs">
          <VoicePickerButton
            label="Default voice"
            value={engineVoices.some((v) => v.id === voice) ? voice : ""}
            onSelect={(voiceId, engine) => {
              onSaveVoice(voiceId);
              onSaveEngine(engine);
            }}
          />
        </div>
        {voiceLabel && <span className="text-xs text-muted-foreground">{voiceLabel}</span>}
      </ControlRow>
      {engineError && <p className="text-xs text-destructive">Voices unavailable: {engineError}</p>}
      <p className="text-xs text-muted-foreground">
        Edge needs internet at synthesis time; Piper downloads a small model once and works offline.
      </p>
    </SettingsSection>
  );
}

export function VideoSection() {
  return (
    <SettingsSection id="video-subheading" title="Video Output & Hardware Acceleration">
      <InfoRow
        label="Container Format"
        value="MP4 (H.264 / AAC Audio)"
        title="Standard MP4 format compatible with all players"
      />
      <InfoRow label="Resolution Preset" value="Original (Preserves Source 1080p / 4K)" />
      <InfoRow
        label="Hardware Encoder"
        value="NVENC (NVIDIA GPU Acceleration)"
        title="Uses hardware NVENC chip for 5x faster rendering"
      />
      <InfoRow label="Color Subsampling" value="YUV420p (Broad compatibility)" />
    </SettingsSection>
  );
}

export function SubtitleSection() {
  return (
    <SettingsSection id="subtitle-subheading" title="Subtitle Design & Overlay">
      <InfoRow
        label="Font Family"
        value="Arial / Roboto / Segoe UI"
        title="Clean sans-serif fonts for optimal readability"
      />
      <InfoRow label="Default Font Size" value="44px (PlayRes 1080p)" />
      <InfoRow label="Position Preset" value="Bottom Center (Margin-V 50px)" />
      <InfoRow label="Background Shadow" value="Dark Outline + Box (High contrast)" />
    </SettingsSection>
  );
}

// ---- PROCESSING ------------------------------------------------------------

export function ProcessingSection({
  settings,
  worker,
  onSaveModel,
  onSaveDevice,
  onSavePreset,
  onSaveGpu,
  onRestartWorker,
  restarting,
  onSaveOrchestrator,
}: {
  settings: SettingsSnapshot;
  worker: WorkerStateInfo | null;
  onSaveModel: (value: string) => void;
  onSaveDevice: (value: string) => void;
  onSavePreset: (value: string) => void;
  onSaveGpu: (value: string) => void;
  onRestartWorker: () => void;
  restarting: boolean;
  onSaveOrchestrator: (value: boolean) => void;
}) {
  const status = workerStateLabel(worker?.state ?? "stopped");
  return (
    <SettingsSection
      id="processing-subheading"
      title="AI Models & Hardware Compute"
      description="Applies to new jobs; the worker runs heavy jobs using local GPU/CPU resources."
    >
      <ControlRow label="Worker Status">
        <span className="text-sm font-semibold text-emerald-400">{status.label}</span>
        {worker?.pid != null && (
          <span className="text-xs font-mono text-muted-foreground">PID {worker.pid}</span>
        )}
        <button
          type="button"
          data-role="worker-restart"
          className="rounded-lg border border-border bg-card hover:bg-accent px-3 py-1 text-xs font-semibold"
          disabled={restarting}
          onClick={onRestartWorker}
        >
          {restarting ? "Restarting…" : "Restart Worker"}
        </button>
      </ControlRow>
      <ControlRow label="STT model">
        <select
          data-role="ai-model"
          className="rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs font-medium"
          value={settings["ai.model"]}
          onChange={(event) => onSaveModel(event.target.value)}
        >
          <option value="large-v3">Whisper Large-v3 (Highest Accuracy · 4GB VRAM)</option>
          <option value="turbo">Whisper Turbo (Fast &amp; Accurate · 2GB VRAM)</option>
          <option value="small">Whisper Small (Lightweight · 1GB VRAM)</option>
        </select>
      </ControlRow>
      <ControlRow label="Compute Device">
        <select
          data-role="ai-device"
          className="rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs font-medium"
          value={settings["ai.device"]}
          onChange={(event) => onSaveDevice(event.target.value)}
        >
          <option value="auto">Auto (Detect NVIDIA CUDA GPU)</option>
          <option value="cuda">CUDA (NVIDIA Hardware Acceleration)</option>
          <option value="cpu">CPU (Multi-threaded Fallback)</option>
        </select>
      </ControlRow>
      <ControlRow label="GPU Override">
        <select
          data-role="gpu-override"
          className="rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs font-medium"
          value={settings["gpu.override"]}
          onChange={(event) => onSaveGpu(event.target.value)}
        >
          <option value="auto">Auto (Default System Hardware)</option>
          <option value="cuda">CUDA (Force GPU Acceleration)</option>
          <option value="cpu">CPU Only (Force Software Engine)</option>
        </select>
      </ControlRow>
      <ControlRow label="Quality Preset">
        <select
          data-role="ai-preset"
          className="rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs font-medium"
          value={settings["ai.preset"]}
          onChange={(event) => onSavePreset(event.target.value)}
        >
          <option value="fast">Fast (Speed Optimized)</option>
          <option value="balanced">Balanced (Recommended)</option>
          <option value="high">High Quality (Production Grade)</option>
        </select>
      </ControlRow>
      <InfoRow label="Parallel jobs" value="1 at a time (single worker, FIFO)" />
      <ControlRow label="Orchestrator v2 (experimental)">
        <input
          data-role="automation-orchestrator-v2"
          type="checkbox"
          checked={Boolean(settings["automation.orchestrator_v2"])}
          onChange={(e) => onSaveOrchestrator(e.target.checked)}
        />
        <span className="text-xs text-muted-foreground">
          Rust owns DAG, intra-job parallel (global 3)
        </span>
      </ControlRow>
    </SettingsSection>
  );
}

// ---- STORAGE ---------------------------------------------------------------

export function StorageSection({
  quotaBytes,
  onSaveQuota,
}: {
  quotaBytes: number;
  onSaveQuota: (value: string) => void;
}) {
  const quotaGb = quotaBytes / (1024 * 1024 * 1024);
  return (
    <SettingsSection id="storage-subheading" title="Storage">
      <ControlRow label="Cache quota">
        <input
          id="cache-quota"
          data-role="cache-quota"
          type="number"
          min={1}
          className="w-32 rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          defaultValue={Math.max(1, Math.round(quotaGb))}
        />
        <span className="text-xs text-muted-foreground">GB</span>
        <button
          type="button"
          data-role="cache-quota-save"
          className="rounded-md border border-border px-3 py-1.5 text-sm"
          onClick={() => {
            const input = document.getElementById("cache-quota") as HTMLInputElement | null;
            if (input?.value) {
              onSaveQuota(String(Math.round(Number(input.value) * 1024 * 1024 * 1024)));
            }
          }}
        >
          Apply
        </button>
      </ControlRow>
      <InfoRow label="Output directory" value="Per project: {data}/projects/{id}/output" />
      <InfoRow label="Models" value="Downloaded on first use into the app data directory" />
    </SettingsSection>
  );
}

// ---- PRIVACY ---------------------------------------------------------------

export function PrivacySection({
  settings,
  onSaveMode,
  onSaveTelemetry,
}: {
  settings: SettingsSnapshot;
  onSaveMode: (value: string) => void;
  onSaveTelemetry: (value: boolean) => void;
}) {
  return (
    <SettingsSection
      id="privacy-subheading"
      title="Privacy"
      description="STT and rendering always run locally. Translation uses the selected provider."
    >
      <ControlRow label="Processing mode">
        <select
          data-role="privacy-mode"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={settings["privacy.mode"]}
          onChange={(event) => onSaveMode(event.target.value)}
        >
          <option value="local">Local</option>
          <option value="cloud">Cloud</option>
        </select>
      </ControlRow>
      <ControlRow label="Telemetry">
        <input
          data-role="privacy-telemetry"
          type="checkbox"
          checked={settings["privacy.telemetry"]}
          onChange={(event) => onSaveTelemetry(event.target.checked)}
        />
      </ControlRow>
    </SettingsSection>
  );
}

// ---- SECURITY --------------------------------------------------------------

export function SecuritySection() {
  return (
    <SettingsSection id="security-subheading" title="Security">
      <InfoRow label="API keys" value="Windows Credential Manager (OS vault)" />
      <InfoRow label="Logging" value="Secrets are never logged" />
      <InfoRow label="Media access" value="Scoped media:// protocol — project files only" />
    </SettingsSection>
  );
}
