import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/api/invoke", () => ({
  safeInvoke: vi.fn(),
}));

import { safeInvoke } from "@/api/invoke";
import { exportSubtitles, exportVideo, type ExportVideoResult } from "./export";

const mockedInvoke = vi.mocked(safeInvoke);

const VIDEO_RESULT: ExportVideoResult = {
  path: "C:\\out\\final.mp4",
  qc: { passed: true, issues: [], warnings: [] },
};

describe("export bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("exports a video with defaults (qc on, no rename)", async () => {
    mockedInvoke.mockResolvedValue(VIDEO_RESULT);
    const result = await exportVideo("C:\\render\\out.mp4", "C:\\out");
    expect(mockedInvoke).toHaveBeenCalledWith("export.video", {
      sourceVideo: "C:\\render\\out.mp4",
      targetDir: "C:\\out",
      name: null,
      runQc: null,
    });
    expect(result.qc.passed).toBe(true);
  });

  it("passes rename and runQc options through", async () => {
    mockedInvoke.mockResolvedValue(VIDEO_RESULT);
    await exportVideo("C:\\render\\out.mp4", "C:\\out", { name: "final", runQc: false });
    expect(mockedInvoke).toHaveBeenCalledWith("export.video", {
      sourceVideo: "C:\\render\\out.mp4",
      targetDir: "C:\\out",
      name: "final",
      runQc: false,
    });
  });

  it("surfaces a QC failure result without throwing", async () => {
    mockedInvoke.mockResolvedValue({
      path: "C:\\out\\final.mp4",
      qc: { passed: false, issues: ["duration drifted 2.50s"], warnings: [] },
    });
    const result = await exportVideo("C:\\render\\out.mp4", "C:\\out");
    expect(result.qc.passed).toBe(false);
    expect(result.qc.issues[0]).toContain("duration");
  });

  it("exports subtitles with a target format", async () => {
    mockedInvoke.mockResolvedValue("C:\\out\\subtitle.vtt");
    const path = await exportSubtitles("C:\\subs\\subtitle.srt", "C:\\out", { format: "vtt" });
    expect(mockedInvoke).toHaveBeenCalledWith("export.subtitles", {
      sourceSubtitle: "C:\\subs\\subtitle.srt",
      targetDir: "C:\\out",
      name: null,
      format: "vtt",
    });
    expect(path).toBe("C:\\out\\subtitle.vtt");
  });

  it("passes null options when exporting subtitles with defaults", async () => {
    mockedInvoke.mockResolvedValue("C:\\out\\subtitle.srt");
    await exportSubtitles("C:\\subs\\subtitle.srt", "C:\\out");
    expect(mockedInvoke).toHaveBeenCalledWith("export.subtitles", {
      sourceSubtitle: "C:\\subs\\subtitle.srt",
      targetDir: "C:\\out",
      name: null,
      format: null,
    });
  });
});
