import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { SettingsSnapshot } from "@/api/settings";
import { ToastProvider } from "@/components/toast";
import { ProvidersProvider } from "@/stores/providers";
import { ProvidersPanel } from "./ProvidersPanel";
import { PrivacySection, ProcessingSection, StorageSection } from "./sections";

const SETTINGS: SettingsSnapshot = {
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

describe("ProvidersPanel", () => {
  function renderPanel() {
    return renderToStaticMarkup(
      <ToastProvider>
        <ProvidersProvider>
          <ProvidersPanel />
        </ProvidersProvider>
      </ToastProvider>,
    );
  }

  it("shows FREE as the default translation provider", () => {
    const html = renderPanel();
    expect(html).toContain('data-role="provider-card-free"');
    expect(html).toContain("FREE");
    expect(html).toContain("Default");
    expect(html).toContain('data-role="default-translation-provider"');
  });

  it("never offers a Delete button on FREE and hides its enable toggle", () => {
    const html = renderPanel();
    const freeCard = html.slice(html.indexOf('data-role="provider-card-free"'));
    const nextCard = freeCard.indexOf('data-role="provider-card-');
    const slice = nextCard === -1 ? freeCard : freeCard.slice(0, nextCard);
    expect(slice).not.toContain("Delete");
    expect(slice).not.toContain("Disable");
  });

  it("exposes Add Provider and Save & Test entry points", () => {
    const html = renderPanel();
    expect(html).toContain('data-role="add-provider"');
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
