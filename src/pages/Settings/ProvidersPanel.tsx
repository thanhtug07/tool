import { useCallback, useMemo, useState } from "react";

import {
  createProvider,
  deleteProvider,
  setProviderDefault,
  setProviderEnabled,
  testProvider,
  updateProvider,
  type ProviderCapability,
  type ProviderInput,
  type ProviderKind,
  type ProviderView,
} from "@/api/provider";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/toast";
import { useProviders } from "@/stores/providers";

const CAPABILITY_LABELS: Record<ProviderCapability, string> = {
  translation: "Translation",
  stt: "STT",
  tts: "TTS",
};

const KIND_LABELS: Record<ProviderKind, string> = {
  free: "FREE (built-in)",
  gemini: "Gemini (cloud)",
  local: "Local LLM (llama.cpp / OpenAI-compatible)",
  mock: "Mock (offline test)",
};

const INPUT_CLS =
  "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm";
const LABEL_CLS = "text-xs font-medium text-muted-foreground";

function capLabel(cap: string): string {
  return CAPABILITY_LABELS[cap as ProviderCapability] ?? cap;
}

function kindLabel(kind: string): string {
  return KIND_LABELS[kind as ProviderKind] ?? kind;
}

// ---- form state ------------------------------------------------------------

type FormState = {
  name: string;
  provider_kind: ProviderKind;
  capabilities: ProviderCapability[];
  base_url: string;
  model: string;
  config: string;
  api_key: string;
  test: boolean;
};

const EMPTY_FORM: FormState = {
  name: "",
  provider_kind: "gemini",
  capabilities: ["translation"],
  base_url: "",
  model: "",
  config: "{}",
  api_key: "",
  test: false,
};

function formFromProvider(p: ProviderView): FormState {
  return {
    name: p.name,
    provider_kind: p.provider_kind,
    capabilities: p.capabilities,
    base_url: p.base_url ?? "",
    model: p.model ?? "",
    config: JSON.stringify(p.config ?? {}, null, 2),
    api_key: "",
    test: false,
  };
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto rounded-lg border border-border bg-card p-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-base font-semibold">{title}</h3>
          <button
            type="button"
            aria-label="Close"
            className="rounded-md px-2 py-1 text-muted-foreground hover:bg-accent"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ---- main panel ------------------------------------------------------------

export function ProvidersPanel() {
  const toast = useToast();
  const { providers, defaults, providersFor, defaultFor, refresh } = useProviders();
  const [form, setForm] = useState<FormState | null>(null);
  const [editing, setEditing] = useState<ProviderView | null>(null);
  const [deleting, setDeleting] = useState<ProviderView | null>(null);
  const [busy, setBusy] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  const translationOptions = useMemo(() => providersFor("translation"), [providersFor, providers]);
  const translationDefault = defaultFor("translation");
  const saveBusy = busy && form !== null;

  const apply = useCallback(
    async (input: ProviderInput, runTest: boolean) => {
      setBusy(true);
      try {
        if (editing) {
          await updateProvider(editing.id, input, runTest);
          toast.push(
            runTest ? "Provider updated and test passed" : "Provider updated",
            "success",
          );
        } else {
          await createProvider(input, runTest);
          toast.push(
            runTest ? "Provider created and test passed" : "Provider created",
            "success",
          );
        }
        setForm(null);
        setEditing(null);
        await refresh();
      } catch (error) {
        toast.push(String(error), "error");
      } finally {
        setBusy(false);
      }
    },
    [editing, refresh, toast],
  );

  const handleSubmit = useCallback(
    (runTest: boolean) => {
      if (!form) return;
      let config: Record<string, unknown>;
      try {
        config = JSON.parse(form.config) as Record<string, unknown>;
      } catch {
        toast.push("Configuration must be valid JSON", "error");
        return;
      }
      void apply(
        {
          name: form.name.trim(),
          provider_type: "translation",
          provider_kind: form.provider_kind,
          capabilities: form.capabilities,
          base_url: form.base_url.trim() || null,
          model: form.model.trim() || null,
          config,
          api_key: form.api_key.trim() || null,
        },
        runTest,
      );
    },
    [apply, form, toast],
  );

  const handleDelete = useCallback(async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await deleteProvider(deleting.id);
      toast.push(`Provider "${deleting.name}" deleted`, "success");
      setDeleting(null);
      await refresh();
    } catch (error) {
      toast.push(String(error), "error");
    } finally {
      setBusy(false);
    }
  }, [deleting, refresh, toast]);

  const handleTest = useCallback(
    async (p: ProviderView) => {
      setTestingId(p.id);
      try {
        const result = await testProvider(p.id);
        toast.push(
          result.ok
            ? `Test passed (${result.latency_ms} ms): ${result.detail}`
            : `Test failed: ${result.detail}`,
          result.ok ? "success" : "error",
        );
      } catch (error) {
        toast.push(`Test failed: ${String(error)}`, "error");
      } finally {
        setTestingId(null);
        await refresh();
      }
    },
    [refresh, toast],
  );

  const handleSetDefault = useCallback(
    async (id: string) => {
      try {
        await setProviderDefault(id, "translation");
        toast.push("Default translation provider updated", "success");
        await refresh();
      } catch (error) {
        toast.push(String(error), "error");
      }
    },
    [refresh, toast],
  );

  const handleToggle = useCallback(
    async (p: ProviderView) => {
      try {
        await setProviderEnabled(p.id, !p.enabled);
        await refresh();
      } catch (error) {
        toast.push(String(error), "error");
      }
    },
    [refresh, toast],
  );

  return (
    <div className="space-y-4">
      {/* Defaults per capability */}
      <div className="space-y-2 rounded-lg border border-border bg-card p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Default providers
        </p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="w-28 shrink-0 text-muted-foreground">Translation</span>
          <select
            data-role="default-translation-provider"
            className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            value={translationDefault?.id ?? "free"}
            onChange={(e) => void handleSetDefault(e.target.value)}
          >
            {translationOptions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.provider_kind === "free" ? " (local, free)" : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="w-28 shrink-0 text-muted-foreground">STT</span>
          <span className="text-sm">Local (faster-whisper — built-in)</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="w-28 shrink-0 text-muted-foreground">TTS</span>
          <span className="text-sm text-muted-foreground">Not available in this build</span>
        </div>
      </div>

      {/* Add */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Connected providers
        </p>
        <Button
          data-role="add-provider"
          size="sm"
          onClick={() => {
            setEditing(null);
            setForm(EMPTY_FORM);
          }}
        >
          + Add Provider
        </Button>
      </div>

      {/* Cards */}
      <ul className="space-y-2">
        {providers.map((p) => {
          const isDefault = defaults.translation === p.id;
          const isFree = p.id === "free";
          return (
            <li
              key={p.id}
              data-role={`provider-card-${p.id}`}
              className="space-y-2 rounded-lg border border-border bg-card p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{p.name}</span>
                  {isDefault && (
                    <span className="rounded-full bg-primary/20 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
                      Default
                    </span>
                  )}
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                      p.enabled
                        ? "bg-emerald-500/20 text-emerald-400"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {p.enabled ? "Enabled" : "Disabled"}
                  </span>
                  {p.last_test_status && (
                    <span
                      title={p.last_test_at ?? undefined}
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${
                        p.last_test_status === "success"
                          ? "bg-emerald-500/20 text-emerald-400"
                          : "bg-destructive/20 text-destructive"
                      }`}
                    >
                      Last test: {p.last_test_status}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={testingId === p.id}
                    onClick={() => void handleTest(p)}
                  >
                    {testingId === p.id ? "Testing…" : "Test"}
                  </Button>
                  {!isFree && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void handleToggle(p)}
                    >
                      {p.enabled ? "Disable" : "Enable"}
                    </Button>
                  )}
                  {!isDefault && !isFree && (
                    <Button size="sm" variant="outline" onClick={() => void handleSetDefault(p.id)}>
                      Set Default
                    </Button>
                  )}
                  {!isFree && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditing(p);
                        setForm(formFromProvider(p));
                      }}
                    >
                      Configure
                    </Button>
                  )}
                  {isFree && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditing(p);
                        setForm(formFromProvider(p));
                      }}
                    >
                      Configure
                    </Button>
                  )}
                  {!isFree && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive"
                      onClick={() => setDeleting(p)}
                    >
                      Delete
                    </Button>
                  )}
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {p.provider_kind === "free"
                  ? "Local / free — no API key required · works offline when a local LLM server is configured"
                  : kindLabel(p.provider_kind)}
                {p.needs_key
                  ? p.api_key_configured
                    ? " · API key configured"
                    : " · API key missing — add one to use this provider"
                  : ""}
              </p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>Capabilities: {p.capabilities.map(capLabel).join(" · ") || "—"}</span>
                {p.model && <span>Model: {p.model}</span>}
                {p.base_url && <span className="truncate">{p.base_url}</span>}
              </div>
            </li>
          );
        })}
      </ul>

      {/* Add / edit modal */}
      {form && (
        <Modal
          title={editing ? `Configure ${editing.name}` : "Add provider"}
          onClose={() => {
            if (!busy) {
              setForm(null);
              setEditing(null);
            }
          }}
        >
          <div className="space-y-3">
            <div>
              <label className={LABEL_CLS} htmlFor="provider-name">
                Provider name
              </label>
              <input
                id="provider-name"
                data-role="provider-name"
                className={INPUT_CLS}
                value={form.name}
                placeholder="e.g. My Gemini"
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label className={LABEL_CLS} htmlFor="provider-kind">
                Provider kind
              </label>
              <select
                id="provider-kind"
                data-role="provider-kind"
                className={INPUT_CLS}
                value={form.provider_kind}
                disabled={editing?.id === "free"}
                onChange={(e) =>
                  setForm({ ...form, provider_kind: e.target.value as ProviderKind })
                }
              >
                <option value="gemini">Gemini (cloud)</option>
                <option value="local">Local LLM (llama.cpp / OpenAI-compatible)</option>
                <option value="mock">Mock (offline test)</option>
              </select>
            </div>
            <div>
              <span className={LABEL_CLS}>Capabilities</span>
              <div className="flex flex-wrap gap-4 pt-1">
                {(Object.keys(CAPABILITY_LABELS) as ProviderCapability[]).map((cap) => (
                  <label key={cap} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      data-role={`capability-${cap}`}
                      checked={form.capabilities.includes(cap)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          capabilities: e.target.checked
                            ? [...form.capabilities, cap]
                            : form.capabilities.filter((c) => c !== cap),
                        })
                      }
                    />
                    {capLabel(cap)}
                    {cap === "translation" && (
                      <span className="text-[10px] text-muted-foreground">(live)</span>
                    )}
                  </label>
                ))}
              </div>
              <p className="pt-1 text-[10px] text-muted-foreground">
                STT / TTS capabilities are stored for future builds; only Translation is active now.
              </p>
            </div>
            {form.provider_kind !== "mock" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className={LABEL_CLS} htmlFor="provider-base-url">
                    Base URL (local server or API endpoint)
                  </label>
                  <input
                    id="provider-base-url"
                    data-role="provider-base-url"
                    className={INPUT_CLS}
                    placeholder={form.provider_kind === "gemini" ? "(Gemini default)" : "http://127.0.0.1:8080"}
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  />
                </div>
                <div>
                  <label className={LABEL_CLS} htmlFor="provider-model">
                    Model
                  </label>
                  <input
                    id="provider-model"
                    data-role="provider-model"
                    className={INPUT_CLS}
                    placeholder="gemini-2.5-flash-lite"
                    value={form.model}
                    onChange={(e) => setForm({ ...form, model: e.target.value })}
                  />
                </div>
              </div>
            )}
            {form.provider_kind === "gemini" && (
              <div>
                <label className={LABEL_CLS} htmlFor="provider-api-key">
                  API key
                </label>
                <input
                  id="provider-api-key"
                  data-role="provider-api-key"
                  type="password"
                  className={INPUT_CLS}
                  placeholder={
                    editing?.api_key_configured
                      ? "•••••••• (stored) — leave empty to keep"
                      : "Paste key…"
                  }
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                />
                <p className="pt-1 text-[10px] text-muted-foreground">
                  Stored in the OS credential vault (Windows Credential Manager) — never in the
                  database, never shown back. With “Save &amp; Test” the key is stored only if the
                  test passes.
                </p>
              </div>
            )}
            <div>
              <label className={LABEL_CLS} htmlFor="provider-config">
                Additional configuration (JSON)
              </label>
              <textarea
                id="provider-config"
                data-role="provider-config"
                className={`${INPUT_CLS} font-mono`}
                rows={3}
                value={form.config}
                onChange={(e) => setForm({ ...form, config: e.target.value })}
              />
              <p className="pt-1 text-[10px] text-muted-foreground">
                e.g. {"{"}"model_path": "C:/models/q4.gguf"{"}"} for a local model file.
              </p>
            </div>
            <div className="flex flex-wrap justify-end gap-2 pt-1">
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setForm(null);
                  setEditing(null);
                }}
              >
                Cancel
              </Button>
              <Button
                data-role="provider-save-test"
                variant="outline"
                disabled={saveBusy || form.name.trim().length === 0}
                onClick={() => handleSubmit(true)}
              >
                {saveBusy ? "Testing…" : "Save & Test"}
              </Button>
              <Button
                data-role="provider-save"
                disabled={saveBusy || form.name.trim().length === 0}
                onClick={() => handleSubmit(false)}
              >
                {saveBusy ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Delete confirmation */}
      {deleting && (
        <Modal title={`Delete "${deleting.name}"?`} onClose={() => !busy && setDeleting(null)}>
          <p className="text-sm text-muted-foreground">
            This provider may be used by automation profiles. If it is the default translation
            provider, the default falls back to FREE.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" disabled={busy} onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              data-role="provider-delete-confirm"
              variant="default"
              disabled={busy}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void handleDelete()}
            >
              {busy ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
