import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import WatermarkConfig, {
  DEFAULT_WATERMARK,
  type WatermarkConfig as WatermarkConfigValue,
} from "./WatermarkConfig";

function render(value: WatermarkConfigValue) {
  return renderToStaticMarkup(<WatermarkConfig value={value} onChange={() => {}} />);
}

describe("WatermarkConfig (unit — pure controlled form)", () => {
  it("renders the kind toggle with None selected by default", () => {
    const html = render(DEFAULT_WATERMARK);
    expect(html).toContain('data-role="watermark-config"');
    expect(html).toContain("None");
    expect(html).toContain("Text");
    expect(html).toContain("Image");
  });

  it("shows text options when kind is text", () => {
    const html = render({ ...DEFAULT_WATERMARK, kind: "text", text: "Hello" });
    expect(html).toContain('data-role="watermark-text"');
    expect(html).toContain('value="Hello"');
    expect(html).toContain("Opacity");
  });

  it("shows image options when kind is image", () => {
    const html = render({
      ...DEFAULT_WATERMARK,
      kind: "image",
      imagePath: "C:/logo.png",
      imageWidth: 240,
    });
    expect(html).toContain('data-role="watermark-image-path"');
    expect(html).toContain('value="C:/logo.png"');
    expect(html).toContain("Target width (px)");
  });

  it("lists all nine named positions plus custom", () => {
    const html = render({ ...DEFAULT_WATERMARK, kind: "text" });
    for (const position of [
      "top-left",
      "top",
      "top-right",
      "left",
      "center",
      "right",
      "bottom-left",
      "bottom",
      "bottom-right",
      "custom",
    ]) {
      expect(html).toContain(`value="${position}"`);
    }
  });

  it("shows custom x/y fields only for the custom position", () => {
    const custom = render({ ...DEFAULT_WATERMARK, kind: "text", position: "custom", x: 30, y: 40 });
    expect(custom).toContain("x (px)");
    expect(custom).toContain("y (px)");
    expect(custom).toContain('value="30"');
    expect(custom).toContain('value="40"');

    const anchored = render({ ...DEFAULT_WATERMARK, kind: "text", position: "bottom-right" });
    expect(anchored).not.toContain("x (px)");
    expect(anchored).toContain("Margin (px)");
  });

  it("normalizes the stored #RRGGBBAA color for the color input", () => {
    const html = render({ ...DEFAULT_WATERMARK, kind: "text", color: "#FF0000FF" });
    expect(html).toContain('type="color"');
    expect(html).toContain('value="#ff0000"');
  });

  it("hides all watermark options when kind is none", () => {
    const html = render(DEFAULT_WATERMARK);
    expect(html).not.toContain("Opacity");
    expect(html).not.toContain("Margin (px)");
    expect(html).not.toContain('data-role="watermark-text"');
  });
});
