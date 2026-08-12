import type { ReactNode } from "react";

import type { ApiProvider, SettingsSnapshot } from "@/api/settings";
import type { WorkerStateInfo } from "@/api/worker";
import { workerStateLabel } from "@/components/layout/Sidebar";

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

// ---- AI PROVIDERS ----------------------------------------------------------

export function ProvidersSection({
  provider,
  maskedKey,
  baseUrl,
  keyDraft,
  saving,
  onProviderChange,
  onBaseUrlChange,
  onSaveBaseUrl,
  onKeyDraftChange,
  onSaveKey,
  onDeleteKey,
}: {
  provider: ApiProvider;
  maskedKey: string | null;
  baseUrl: string;
  keyDraft: string;
  saving: boolean;
  onProviderChange: (provider: ApiProvider) => void;
  onBaseUrlChange: (value: string) => void;
  onSaveBaseUrl: () => void;
  onKeyDraftChange: (value: string) => void;
  onSaveKey: () => void;
  onDeleteKey: () => void;
}) {
  return (
    <SettingsSection
      id="providers-subheading"
      title="AI providers"
      description="API keys are stored in the Windows Credential Manager — never in the database."
    >
      <ControlRow label="Provider">
        <select
          data-role="api-provider"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={provider}
          onChange={(event) => onProviderChange(event.target.value as ApiProvider)}
        >
          <option value="gemini">Gemini (translation, MVP)</option>
          <option value="local">Local LLM (llama.cpp / OpenAI-compatible)</option>
          <option value="openai">OpenAI (post-MVP)</option>
        </select>
      </ControlRow>
      <ControlRow label="API key">
        <input
          data-role="api-key-input"
          type="password"
          className="w-72 rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          placeholder={maskedKey ? `${maskedKey} (stored)` : "Paste key…"}
          value={keyDraft}
          onChange={(event) => onKeyDraftChange(event.target.value)}
        />
        <button
          type="button"
          data-role="api-key-save"
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
          disabled={saving || keyDraft.trim().length === 0}
          onClick={onSaveKey}
        >
          {saving ? "Saving…" : "Save key"}
        </button>
        {maskedKey && (
          <button
            type="button"
            data-role="api-key-delete"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onDeleteKey}
          >
            Remove
          </button>
        )}
      </ControlRow>
      {maskedKey ? (
        <InfoRow label="Connection" value={`Key stored (${maskedKey})`} />
      ) : (
        <InfoRow label="Connection" value="No key stored — provider unavailable" />
      )}
      <ControlRow label="Base URL">
        <input
          data-role="api-base-url"
          className="w-72 rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          placeholder="(provider default)"
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
        />
        <button
          type="button"
          data-role="api-base-url-save"
          className="rounded-md border border-border px-3 py-1.5 text-sm"
          onClick={onSaveBaseUrl}
        >
          Save
        </button>
      </ControlRow>
      <InfoRow label="STT provider" value="faster-whisper (local — no key required)" />
      <InfoRow label="TTS provider" value="Not available in this build" />
    </SettingsSection>
  );
}

// ---- VOICE / SUBTITLE / VIDEO (backend-fixed) ------------------------------

export function VoiceSection() {
  return (
    <SettingsSection id="voice-subheading" title="Voice">
      <ComingSoon
        label="Voice selection"
        note="Default voice and dubbing options require the TTS pipeline — not available in this build."
      />
    </SettingsSection>
  );
}

export function VideoSection() {
  return (
    <SettingsSection id="video-subheading" title="Video">
      <InfoRow label="Default output format" value="MP4 (H.264)" />
      <InfoRow label="Resolution" value="Preserves source" />
      <InfoRow label="FPS" value="Preserves source" />
    </SettingsSection>
  );
}

export function SubtitleSection() {
  return (
    <SettingsSection id="subtitle-subheading" title="Subtitle">
      <InfoRow label="Style" value="Per-language defaults (e.g. Arial 44px, bottom center)" />
      <InfoRow label="Burn-in" value="Toggled per run in Automation" />
      <InfoRow label="Custom styling" value="Not available in this build" />
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
}: {
  settings: SettingsSnapshot;
  worker: WorkerStateInfo | null;
  onSaveModel: (value: string) => void;
  onSaveDevice: (value: string) => void;
  onSavePreset: (value: string) => void;
  onSaveGpu: (value: string) => void;
  onRestartWorker: () => void;
  restarting: boolean;
}) {
  const status = workerStateLabel(worker?.state ?? "stopped");
  return (
    <SettingsSection
      id="processing-subheading"
      title="Processing"
      description="Applies to new jobs; the worker runs one heavy job at a time."
    >
      <ControlRow label="Worker">
        <span className="text-sm font-medium">{status.label}</span>
        {worker?.pid != null && (
          <span className="text-xs text-muted-foreground">PID {worker.pid}</span>
        )}
        <button
          type="button"
          data-role="worker-restart"
          className="rounded-md border border-border px-3 py-1.5 text-sm"
          disabled={restarting}
          onClick={onRestartWorker}
        >
          {restarting ? "Restarting…" : "Restart worker"}
        </button>
      </ControlRow>
      <ControlRow label="STT model">
        <select
          data-role="ai-model"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={settings["ai.model"]}
          onChange={(event) => onSaveModel(event.target.value)}
        >
          {["large-v3", "turbo", "small"].map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
      </ControlRow>
      <ControlRow label="Compute device">
        <select
          data-role="ai-device"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={settings["ai.device"]}
          onChange={(event) => onSaveDevice(event.target.value)}
        >
          {["auto", "cuda", "cpu"].map((device) => (
            <option key={device} value={device}>
              {device}
            </option>
          ))}
        </select>
      </ControlRow>
      <ControlRow label="GPU override">
        <select
          data-role="gpu-override"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={settings["gpu.override"]}
          onChange={(event) => onSaveGpu(event.target.value)}
        >
          {["auto", "cuda", "cpu"].map((device) => (
            <option key={device} value={device}>
              {device}
            </option>
          ))}
        </select>
      </ControlRow>
      <ControlRow label="Quality preset">
        <select
          data-role="ai-preset"
          className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
          value={settings["ai.preset"]}
          onChange={(event) => onSavePreset(event.target.value)}
        >
          {["fast", "balanced", "high"].map((preset) => (
            <option key={preset} value={preset}>
              {preset}
            </option>
          ))}
        </select>
      </ControlRow>
      <InfoRow label="Parallel jobs" value="1 at a time (single worker, FIFO)" />
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
