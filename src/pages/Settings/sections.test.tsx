import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { SettingsSnapshot } from "@/api/settings";
import { PrivacySection, ProcessingSection, ProvidersSection, StorageSection } from "./sections";

const SETTINGS: SettingsSnapshot = {
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

describe("ProcessingSection", () => {
  it("renders model/device/preset/GPU controls with current values", () => {
    const html = renderToStaticMarkup(
      <ProcessingSection
        settings={SETTINGS}
        worker={null}
        onSaveModel={() => {}}
        onSaveDevice={() => {}}
        onSavePreset={() => {}}
        onSaveGpu={() => {}}
        onRestartWorker={() => {}}
        restarting={false}
      />,
    );
    expect(html).toContain("STT model");
    expect(html).toContain('data-role="ai-model"');
    expect(html).toContain('data-role="ai-device"');
    expect(html).toContain('data-role="ai-preset"');
    expect(html).toContain('data-role="gpu-override"');
    expect(html).toContain('data-role="worker-restart"');
  });
});

describe("ProvidersSection", () => {
  it("shows a masked stored key and never the full secret", () => {
    const html = renderToStaticMarkup(
      <ProvidersSection
        provider="gemini"
        maskedKey="AIz****wxyz"
        baseUrl=""
        keyDraft=""
        saving={false}
        onProviderChange={() => {}}
        onBaseUrlChange={() => {}}
        onSaveBaseUrl={() => {}}
        onKeyDraftChange={() => {}}
        onSaveKey={() => {}}
        onDeleteKey={() => {}}
      />,
    );
    expect(html).toContain("AIz****wxyz");
    expect(html).toContain('data-role="api-key-input"');
    expect(html).toContain('data-role="api-key-delete"');
    // The middle of the secret must not appear anywhere.
    expect(html).not.toContain("abcdefghijklmn");
  });

  it("disables save when the key draft is empty", () => {
    const html = renderToStaticMarkup(
      <ProvidersSection
        provider="gemini"
        maskedKey={null}
        baseUrl=""
        keyDraft=""
        saving={false}
        onProviderChange={() => {}}
        onBaseUrlChange={() => {}}
        onSaveBaseUrl={() => {}}
        onKeyDraftChange={() => {}}
        onSaveKey={() => {}}
        onDeleteKey={() => {}}
      />,
    );
    expect(html.match(/data-role="api-key-save"[^>]*disabled=""/g) ?? []).toHaveLength(1);
    expect(html).not.toContain('data-role="api-key-delete"');
  });

  it("offers the MVP providers", () => {
    const html = renderToStaticMarkup(
      <ProvidersSection
        provider="gemini"
        maskedKey={null}
        baseUrl=""
        keyDraft=""
        saving={false}
        onProviderChange={() => {}}
        onBaseUrlChange={() => {}}
        onSaveBaseUrl={() => {}}
        onKeyDraftChange={() => {}}
        onSaveKey={() => {}}
        onDeleteKey={() => {}}
      />,
    );
    expect(html).toContain("Gemini (translation, MVP)");
    expect(html).toContain("Local LLM");
    expect(html).toContain("OpenAI (post-MVP)");
  });
});

describe("StorageSection", () => {
  it("renders the quota control", () => {
    const html = renderToStaticMarkup(
      <StorageSection quotaBytes={5368709120} onSaveQuota={() => {}} />,
    );
    expect(html).toContain("Cache quota");
    expect(html).toContain('data-role="cache-quota"');
    expect(html).toContain('data-role="cache-quota-save"');
  });
});

describe("PrivacySection", () => {
  it("renders mode and telemetry controls with current values", () => {
    const html = renderToStaticMarkup(
      <PrivacySection settings={SETTINGS} onSaveMode={() => {}} onSaveTelemetry={() => {}} />,
    );
    expect(html).toContain("Processing mode");
    expect(html).toContain('data-role="privacy-mode"');
    expect(html).toContain('data-role="privacy-telemetry"');
  });
});
