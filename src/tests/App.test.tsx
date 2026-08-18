import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App";
import { ToastProvider } from "../components/toast";

describe("App", () => {
  it("renders the studio shell with the top bar and its navigation areas", () => {
    const html = renderToStaticMarkup(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );
    expect(html).toContain("AutoTranslate");
    expect(html).toContain("Home");
    expect(html).toContain("Automation");
    expect(html).toContain("Custom");
    expect(html).toContain("Settings");
    expect(html).toContain('data-role="top-bar"');
    expect(html).toContain('data-role="project-select"');
    expect(html).toContain('data-role="export-button"');
  });

  it("opens on the Home project hub by default", () => {
    const html = renderToStaticMarkup(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );
    expect(html).toContain("New Automation");
    expect(html).toContain("Recent projects");
    expect(html).toContain("Custom workflow");
  });
});
