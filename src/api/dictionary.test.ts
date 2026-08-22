import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/api/invoke", () => ({
  safeInvoke: vi.fn(),
}));

import { safeInvoke } from "@/api/invoke";
import {
  characterDelete,
  characterList,
  characterUpsert,
  glossaryDelete,
  glossaryList,
  glossaryUpsert,
} from "./dictionary";

const mockedInvoke = vi.mocked(safeInvoke);

const PROJECT = "00000000-0000-4000-8000-000000000000";

describe("dictionary bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("lists glossary entries for a project", async () => {
    mockedInvoke.mockResolvedValue([
      { project_id: PROJECT, term: "api", translation: "Giao diện", updated_at: "t" },
    ]);
    const result = await glossaryList(PROJECT);
    expect(mockedInvoke).toHaveBeenCalledWith("dictionary.glossary.list", { projectId: PROJECT });
    expect(result[0].term).toBe("api");
  });

  it("upserts a glossary term (lowercased by the backend)", async () => {
    mockedInvoke.mockResolvedValue({
      project_id: PROJECT,
      term: "api",
      translation: "Giao diện",
      updated_at: "t",
    });
    await glossaryUpsert(PROJECT, "API", "Giao diện");
    expect(mockedInvoke).toHaveBeenCalledWith("dictionary.glossary.upsert", {
      projectId: PROJECT,
      term: "API",
      translation: "Giao diện",
    });
  });

  it("deletes a glossary term", async () => {
    mockedInvoke.mockResolvedValue(undefined);
    await glossaryDelete(PROJECT, "api");
    expect(mockedInvoke).toHaveBeenCalledWith("dictionary.glossary.delete", {
      projectId: PROJECT,
      term: "api",
    });
  });

  it("lists characters for a project", async () => {
    mockedInvoke.mockResolvedValue([
      { project_id: PROJECT, name: "Nam", description: "nhân vật chính", updated_at: "t" },
    ]);
    const result = await characterList(PROJECT);
    expect(mockedInvoke).toHaveBeenCalledWith("dictionary.character.list", { projectId: PROJECT });
    expect(result[0].name).toBe("Nam");
  });

  it("upserts and deletes characters", async () => {
    mockedInvoke
      .mockResolvedValueOnce({
        project_id: PROJECT,
        name: "Nam",
        description: "chính",
        updated_at: "t",
      })
      .mockResolvedValueOnce(undefined);
    await characterUpsert(PROJECT, "Nam", "chính");
    await characterDelete(PROJECT, "Nam");
    expect(mockedInvoke).toHaveBeenNthCalledWith(1, "dictionary.character.upsert", {
      projectId: PROJECT,
      name: "Nam",
      description: "chính",
    });
    expect(mockedInvoke).toHaveBeenNthCalledWith(2, "dictionary.character.delete", {
      projectId: PROJECT,
      name: "Nam",
    });
  });
});
