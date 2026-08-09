import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/api/bridge", () => ({
  ping: vi.fn(),
}));

import { ConnectionStatus, default as SettingsPage } from "./index";

describe("SettingsPage (unit — mocked bridge, no Tauri IPC)", () => {
  it("renders the About section and an idle connection state", () => {
    const html = renderToStaticMarkup(<SettingsPage />);

    expect(html).toContain("Settings");
    expect(html).toContain("About");
    expect(html).toContain("Test connection");
    expect(html).toContain("Not tested yet.");
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
