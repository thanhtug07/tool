import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/api/bridge", () => ({
  ping: vi.fn(),
}));

vi.mock("@/api/settings", () => ({
  getSettings: vi.fn(),
  setSetting: vi.fn(),
  setApiKey: vi.fn(),
  getApiKeyMasked: vi.fn(),
  deleteApiKey: vi.fn(),
}));

import { ToastProvider } from "@/components/toast";
import { ProvidersProvider } from "@/stores/providers";
import { VoicesProvider } from "@/stores/voices";
import { ConnectionStatus, default as SettingsPage } from "./index";

function renderPage(section?: "general" | "providers" | "ai" | "voice" | "processing" | "about") {
  return renderToStaticMarkup(
    <ToastProvider>
      <ProvidersProvider>
        <VoicesProvider>
          <SettingsPage initialSection={section} />
        </VoicesProvider>
      </ProvidersProvider>
    </ToastProvider>,
  );
}

describe("SettingsPage (unit — mocked bridge, no Tauri IPC)", () => {
  it("renders the settings navigation with all sections reachable", () => {
    const html = renderPage();
    expect(html).toContain("Settings");
    expect(html).toContain('data-role="settings-nav-general"');
    expect(html).toContain('data-role="settings-nav-providers"');
    expect(html).toContain('data-role="settings-nav-ai"');
    expect(html).toContain('data-role="settings-nav-subtitle"');
    expect(html).toContain('data-role="settings-nav-video"');
    expect(html).toContain('data-role="settings-nav-storage"');
    expect(html).toContain('data-role="settings-nav-about"');
  });

  it("renders the General section (with Privacy) by default", () => {
    const html = renderPage();
    expect(html).toContain("General");
    expect(html).toContain("Privacy");
  });

  it("renders AI voice defaults; advanced compute is progressive", () => {
    const html = renderPage("ai");
    expect(html).toContain("AI");
    expect(html).toContain("Voice");
    expect(html).toContain('data-role="tts-engine"');
    expect(html).toContain("Advanced");
  });

  it("renders the Providers section and explains the credential vault", () => {
    const html = renderPage("providers");
    expect(html).toContain("Providers");
    expect(html).toContain("Windows Credential Manager");
    expect(html).not.toContain("sk-");
  });

  it("maps legacy voice section to AI", () => {
    const html = renderPage("voice");
    expect(html).toContain('data-role="tts-engine"');
  });

  it("renders About with connection test", () => {
    const html = renderPage("about");
    expect(html).toContain("Connection");
    expect(html).toContain("Test connection");
    expect(html).toContain("Not tested yet.");
  });

  it("renders the toast viewport from the provider", () => {
    const html = renderPage();
    expect(html).toContain('data-role="toast-viewport"');
  });
});

describe("ConnectionStatus (unit — pure presentational states)", () => {
  it("renders the testing state", () => {
    const html = renderToStaticMarkup(<ConnectionStatus state={{ status: "testing" }} />);
    expect(html).toContain("Testing connection");
  });

  it("renders a successful pong result with latency", () => {
    const html = renderToStaticMarkup(
      <ConnectionStatus
        state={{ status: "success", result: { response: "pong", latencyMs: 12 } }}
      />,
    );
    expect(html).toContain("pong (12 ms)");
  });

  it("renders a human-readable error without leaking raw stack traces", () => {
    const html = renderToStaticMarkup(
      <ConnectionStatus
        state={{
          status: "error",
          error: {
            code: "E_IPC_UNAVAILABLE",
            message: "Cannot reach the Rust core. Run the app inside Tauri (npm run tauri dev).",
          },
        }}
      />,
    );
    expect(html).toContain("Cannot reach the Rust core");
    expect(html).not.toContain("__TAURI_INTERNALS__");
  });
});
