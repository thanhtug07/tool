import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { ToastViewport, type Toast } from "./toast";

const TOASTS: Toast[] = [
  { id: 1, kind: "info", message: "Processing started" },
  { id: 2, kind: "error", message: "E_PERMISSION_DENIED" },
  { id: 3, kind: "success", message: "Saved ai.device" },
];

describe("ToastViewport (unit — static render)", () => {
  it("renders nothing when empty", () => {
    const html = renderToStaticMarkup(<ToastViewport toasts={[]} onDismiss={() => {}} />);
    expect(html).toContain('data-role="toast-viewport"');
    expect(html).not.toContain('data-role="toast"');
  });

  it("renders every toast with its kind and a dismiss button", () => {
    const html = renderToStaticMarkup(<ToastViewport toasts={TOASTS} onDismiss={() => {}} />);
    expect(html).toContain("Processing started");
    expect(html).toContain("E_PERMISSION_DENIED");
    expect(html).toContain("Saved ai.device");
    expect(html.match(/data-role="toast-dismiss"/g) ?? []).toHaveLength(3);
    expect(html).toContain('data-kind="error"');
    expect(html).toContain('data-kind="success"');
  });
});
