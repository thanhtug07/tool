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
import { ConnectionStatus, default as SettingsPage } from "./index";

function renderPage() {
  return renderToStaticMarkup(
    <ToastProvider>
      <SettingsPage />
    </ToastProvider>,
  );
}

describe("SettingsPage (unit — mocked bridge, no Tauri IPC)", () => {
  it("renders the Connection section and an idle connection state", () => {
    const html = renderPage();

    expect(html).toContain("Settings");
    expect(html).toContain("Connection");
    expect(html).toContain("Test connection");
    expect(html).toContain("Not tested yet.");
  });

  it("renders the General, Providers, Processing, Storage and Privacy sections", () => {
    const html = renderPage();
    expect(html).toContain("General");
    expect(html).toContain("AI providers");
    expect(html).toContain("Processing");
    expect(html).toContain("Storage");
    expect(html).toContain("Privacy");
    expect(html).toContain("STT model");
    expect(html).toContain("GPU override");
    expect(html).toContain("Restart worker");
    expect(html).toContain("Coming soon");
  });

  it("explains that keys live in the credential vault, not the DB", () => {
    const html = renderPage();
    expect(html).toContain("Windows Credential Manager");
    expect(html).not.toContain("sk-");
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
