import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App";

describe("App", () => {
  it("renders the desktop shell with sidebar navigation", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("AI Video Localization");
    expect(html).toContain("Projects");
    expect(html).toContain("Settings");
    expect(html).toContain("About");
  });
});
