import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App";

describe("App", () => {
  it("renders the foundation scaffold heading", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("AI Video Localization Studio");
  });
});
