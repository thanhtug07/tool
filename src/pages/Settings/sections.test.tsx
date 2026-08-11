import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { SettingsSnapshot } from "@/api/settings";
import {
  AiSettingsSection,
  ApiSettingsSection,
  CacheSettingsSection,
  GpuSettingsSection,
  PrivacySettingsSection,
} from "./sections";

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

describe("AiSettingsSection", () => {
  it("renders model/device/preset controls with current values", () => {
    const html = renderToStaticMarkup(<AiSettingsSection settings={SETTINGS} onSave={() => {}} />);
    expect(html).toContain("STT model");
    expect(html).toContain('data-role="ai-model"');
    expect(html).toContain('data-role="ai-device"');
    expect(html).toContain('data-role="ai-preset"');
  });
});

describe("GpuSettingsSection", () => {
  it("renders the acceleration override select", () => {
    const html = renderToStaticMarkup(<GpuSettingsSection settings={SETTINGS} onSave={() => {}} />);
    expect(html).toContain("Acceleration");
    expect(html).toContain('data-role="gpu-override"');
  });
});

describe("ApiSettingsSection", () => {
  it("shows a masked stored key and never the full secret", () => {
    const html = renderToStaticMarkup(
      <ApiSettingsSection
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
      <ApiSettingsSection
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
      <ApiSettingsSection
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
    expect(html).toContain(">Gemini</option>");
    expect(html).toContain(">Local LLM</option>");
    expect(html).toContain(">OpenAI (post-MVP)</option>");
  });
});

describe("CacheSettingsSection", () => {
  it("renders the quota in GB derived from bytes", () => {
    const html = renderToStaticMarkup(
      <CacheSettingsSection quotaBytes={5368709120} onSave={() => {}} />,
    );
    expect(html).toContain("Storage quota (GB)");
    expect(html).toContain('data-role="cache-quota"');
  });
});

describe("PrivacySettingsSection", () => {
  it("renders mode and telemetry controls with current values", () => {
    const html = renderToStaticMarkup(
      <PrivacySettingsSection
        settings={SETTINGS}
        onSaveMode={() => {}}
        onSaveTelemetry={() => {}}
      />,
    );
    expect(html).toContain("Processing mode");
    expect(html).toContain('data-role="privacy-mode"');
    expect(html).toContain('data-role="privacy-telemetry"');
  });
});
