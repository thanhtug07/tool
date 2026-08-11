import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import ExportView, { qcSummary } from "./ExportView";

describe("ExportView (unit — static render)", () => {
  it("renders the export shell with video and subtitle sections", () => {
    const html = renderToStaticMarkup(<ExportView />);
    expect(html).toContain("Export");
    expect(html).toContain("Target folder");
    expect(html).toContain("Rendered video");
    expect(html).toContain("Export video");
    expect(html).toContain("Subtitle file");
    expect(html).toContain("Export subtitles");
  });

  it("offers SRT, VTT and ASS subtitle formats", () => {
    const html = renderToStaticMarkup(<ExportView />);
    expect(html).toContain(">SRT</option>");
    expect(html).toContain(">VTT</option>");
    expect(html).toContain(">ASS</option>");
  });

  it("disables export buttons until paths are provided", () => {
    const html = renderToStaticMarkup(<ExportView />);
    // Both buttons render with the disabled attribute while paths are empty.
    expect(
      html.match(/data-role="export-(video|subtitle)-button"[^>]*disabled=""/g) ?? [],
    ).toHaveLength(2);
  });

  it("does not render results before an export", () => {
    const html = renderToStaticMarkup(<ExportView />);
    expect(html).not.toContain('data-role="export-video-result"');
    expect(html).not.toContain('data-role="export-subtitle-result"');
    expect(html).not.toContain('data-role="export-error"');
  });
});

describe("qcSummary (pure)", () => {
  it("summarizes a clean pass", () => {
    expect(qcSummary({ passed: true, issues: [], warnings: [] })).toBe("Passed");
  });

  it("counts warnings on a pass", () => {
    expect(
      qcSummary({ passed: true, issues: [], warnings: ["muxed subtitle streams were dropped"] }),
    ).toBe("Passed (1 warning)");
  });

  it("counts issues on a failure", () => {
    expect(qcSummary({ passed: false, issues: ["a", "b"], warnings: [] })).toBe(
      "Failed — 2 issues",
    );
  });
});
