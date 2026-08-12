import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App";

describe("App", () => {
  it("renders the desktop shell with the 4 navigation areas", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("AutoTranslate");
    expect(html).toContain("Dashboard");
    expect(html).toContain("Automation");
    expect(html).toContain("Tools");
    expect(html).toContain("Settings");
  });

  it("opens on the Dashboard with the workspace at a glance", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("Your workspace at a glance.");
    expect(html).toContain("Real-time processing status");
  });
});
