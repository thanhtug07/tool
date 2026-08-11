import type { ApiProvider, SettingsSnapshot } from "@/api/settings";

/** Shared labeled select option row. */
function SelectRow({
  id,
  label,
  value,
  options,
  onSave,
  dataRole,
}: {
  id: string;
  label: string;
  value: string;
  options: readonly string[];
  onSave: (value: string) => void;
  dataRole: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={id} className="w-40 text-sm">
        {label}
      </label>
      <select
        id={id}
        data-role={dataRole}
        className="rounded border border-border bg-background px-2 py-1 text-sm"
        value={value}
        onChange={(event) => onSave(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

/** AI section: STT/translation model, compute device, quality preset. */
export function AiSettingsSection({
  settings,
  onSave,
}: {
  settings: SettingsSnapshot;
  onSave: (key: "ai.model" | "ai.device" | "ai.preset", value: string) => void;
}) {
  return (
    <section aria-labelledby="ai-subheading" className="space-y-2">
      <h2 id="ai-subheading" className="text-sm font-medium">
        AI
      </h2>
      <SelectRow
        id="ai-model"
        label="STT model"
        value={settings["ai.model"]}
        options={["large-v3", "turbo", "small"]}
        onSave={(v) => onSave("ai.model", v)}
        dataRole="ai-model"
      />
      <SelectRow
        id="ai-device"
        label="Compute device"
        value={settings["ai.device"]}
        options={["auto", "cuda", "cpu"]}
        onSave={(v) => onSave("ai.device", v)}
        dataRole="ai-device"
      />
      <SelectRow
        id="ai-preset"
        label="Quality preset"
        value={settings["ai.preset"]}
        options={["fast", "balanced", "high"]}
        onSave={(v) => onSave("ai.preset", v)}
        dataRole="ai-preset"
      />
    </section>
  );
}

/** GPU section: auto / forced backend override. */
export function GpuSettingsSection({
  settings,
  onSave,
}: {
  settings: SettingsSnapshot;
  onSave: (value: string) => void;
}) {
  return (
    <section aria-labelledby="gpu-subheading" className="space-y-2">
      <h2 id="gpu-subheading" className="text-sm font-medium">
        GPU
      </h2>
      <SelectRow
        id="gpu-override"
        label="Acceleration"
        value={settings["gpu.override"]}
        options={["auto", "cuda", "cpu"]}
        onSave={onSave}
        dataRole="gpu-override"
      />
    </section>
  );
}

/** API section: provider + base URL + credential-vault key (masked only). */
export function ApiSettingsSection({
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
    <section aria-labelledby="api-subheading" className="space-y-2">
      <h2 id="api-subheading" className="text-sm font-medium">
        API
      </h2>
      <div className="flex items-center gap-2">
        <label htmlFor="api-provider" className="w-40 text-sm">
          Provider
        </label>
        <select
          id="api-provider"
          data-role="api-provider"
          className="rounded border border-border bg-background px-2 py-1 text-sm"
          value={provider}
          onChange={(event) => onProviderChange(event.target.value as ApiProvider)}
        >
          <option value="gemini">Gemini</option>
          <option value="local">Local LLM</option>
          <option value="openai">OpenAI (post-MVP)</option>
        </select>
      </div>
      <div className="flex items-center gap-2">
        <label htmlFor="api-base-url" className="w-40 text-sm">
          Base URL
        </label>
        <input
          id="api-base-url"
          data-role="api-base-url"
          className="w-72 rounded border border-border bg-background px-2 py-1 text-sm"
          placeholder="(provider default)"
          value={baseUrl}
          onChange={(event) => onBaseUrlChange(event.target.value)}
        />
        <button
          type="button"
          data-role="api-base-url-save"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          onClick={onSaveBaseUrl}
        >
          Save
        </button>
      </div>
      <div className="flex items-center gap-2">
        <label htmlFor="api-key" className="w-40 text-sm">
          API key
        </label>
        <input
          id="api-key"
          data-role="api-key-input"
          type="password"
          className="w-72 rounded border border-border bg-background px-2 py-1 text-sm"
          placeholder={maskedKey ? `${maskedKey} (stored)` : "Paste key…"}
          value={keyDraft}
          onChange={(event) => onKeyDraftChange(event.target.value)}
        />
        <button
          type="button"
          data-role="api-key-save"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
          disabled={saving || keyDraft.trim().length === 0}
          onClick={onSaveKey}
        >
          {saving ? "Saving…" : "Save key"}
        </button>
        {maskedKey && (
          <button
            type="button"
            data-role="api-key-delete"
            className="rounded border border-border px-3 py-1 text-sm"
            onClick={onDeleteKey}
          >
            Remove
          </button>
        )}
      </div>
      <p data-role="api-key-status" className="text-xs text-muted-foreground">
        {maskedKey
          ? `Key stored in Windows Credential Manager (${maskedKey}).`
          : "No key stored. Keys are saved to the OS credential vault, never to the database."}
      </p>
    </section>
  );
}

/** Cache section: LRU quota in GB. */
export function CacheSettingsSection({
  quotaBytes,
  onSave,
}: {
  quotaBytes: number;
  onSave: (value: string) => void;
}) {
  const quotaGb = quotaBytes / (1024 * 1024 * 1024);
  return (
    <section aria-labelledby="cache-subheading" className="space-y-2">
      <h2 id="cache-subheading" className="text-sm font-medium">
        Cache
      </h2>
      <div className="flex items-center gap-2">
        <label htmlFor="cache-quota" className="w-40 text-sm">
          Storage quota (GB)
        </label>
        <input
          id="cache-quota"
          data-role="cache-quota"
          type="number"
          min={1}
          className="w-32 rounded border border-border bg-background px-2 py-1 text-sm"
          defaultValue={Math.max(1, Math.round(quotaGb))}
        />
        <button
          type="button"
          data-role="cache-quota-save"
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
          onClick={() => {
            const input = document.getElementById("cache-quota") as HTMLInputElement | null;
            if (input?.value) {
              onSave(String(Math.round(Number(input.value) * 1024 * 1024 * 1024)));
            }
          }}
        >
          Apply
        </button>
      </div>
    </section>
  );
}

/** Privacy section: processing mode + telemetry. */
export function PrivacySettingsSection({
  settings,
  onSaveMode,
  onSaveTelemetry,
}: {
  settings: SettingsSnapshot;
  onSaveMode: (value: string) => void;
  onSaveTelemetry: (value: boolean) => void;
}) {
  return (
    <section aria-labelledby="privacy-subheading" className="space-y-2">
      <h2 id="privacy-subheading" className="text-sm font-medium">
        Privacy
      </h2>
      <SelectRow
        id="privacy-mode"
        label="Processing mode"
        value={settings["privacy.mode"]}
        options={["local", "cloud"]}
        onSave={onSaveMode}
        dataRole="privacy-mode"
      />
      <div className="flex items-center gap-2">
        <label htmlFor="privacy-telemetry" className="w-40 text-sm">
          Telemetry
        </label>
        <input
          id="privacy-telemetry"
          data-role="privacy-telemetry"
          type="checkbox"
          checked={settings["privacy.telemetry"]}
          onChange={(event) => onSaveTelemetry(event.target.checked)}
        />
      </div>
    </section>
  );
}
