import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import {
  getSubtitleCues,
  replaceSubtitleCues,
  updateSubtitleCue,
  type CuePatch,
  type SubtitleCue,
} from "./subtitle";

const mockedInvoke = vi.mocked(invoke);

const PROJECT = "00000000-0000-4000-8000-000000000000";
const CUE: SubtitleCue = {
  id: "11111111-1111-4111-8111-111111111111",
  project_id: PROJECT,
  cue_number: 1,
  start: 3.2,
  end: 6.4,
  text: "Xin chào",
  speaker: "Nam",
  source_text: "Hello",
  status: "draft",
  style_json: null,
  updated_at: "t",
};

describe("subtitle bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("lists cues for a project ordered by cue number", async () => {
    mockedInvoke.mockResolvedValue([CUE]);
    const result = await getSubtitleCues(PROJECT);
    expect(mockedInvoke).toHaveBeenCalledWith("subtitle.get_cues", { projectId: PROJECT });
    expect(result[0].text).toBe("Xin chào");
  });

  it("replaces a project's cue set atomically", async () => {
    mockedInvoke.mockResolvedValue(2);
    const count = await replaceSubtitleCues(PROJECT, [
      { cue_number: 1, start: 0, end: 1, text: "a" },
      { cue_number: 2, start: 1, end: 2, text: "b" },
    ]);
    expect(mockedInvoke).toHaveBeenCalledWith("subtitle.replace_cues", {
      projectId: PROJECT,
      cues: [
        { cue_number: 1, start: 0, end: 1, text: "a" },
        { cue_number: 2, start: 1, end: 2, text: "b" },
      ],
    });
    expect(count).toBe(2);
  });

  it("updates a cue with an editor patch", async () => {
    mockedInvoke.mockResolvedValue({ ...CUE, text: "Xin chào các bạn", status: "edited" });
    const patch: CuePatch = { text: "Xin chào các bạn", status: "edited" };
    await updateSubtitleCue(CUE.id, patch);
    expect(mockedInvoke).toHaveBeenCalledWith("subtitle.update_cue", { id: CUE.id, patch });
  });

  it("updates timing and speaker in one patch", async () => {
    mockedInvoke.mockResolvedValue(CUE);
    await updateSubtitleCue(CUE.id, { start: 4, end: 8, speaker: "A" });
    expect(mockedInvoke).toHaveBeenCalledWith("subtitle.update_cue", {
      id: CUE.id,
      patch: { start: 4, end: 8, speaker: "A" },
    });
  });
});
