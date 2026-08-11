import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import { createProject, deleteProject, listProjects, openProject, type Project } from "./project";

const mockedInvoke = vi.mocked(invoke);

const PROJECT: Project = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "Sample",
  source_video_path: "C:\\Videos\\sample.mp4",
  status: "draft",
  created_at: "2026-08-01T00:00:00.000Z",
  updated_at: "2026-08-01T00:00:00.000Z",
};

describe("project bridge (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
  });

  it("creates a project from a name and video path", async () => {
    mockedInvoke.mockResolvedValue(PROJECT);
    const project = await createProject("Sample", "C:\\Videos\\sample.mp4");
    expect(mockedInvoke).toHaveBeenCalledWith("project.create", {
      name: "Sample",
      videoPath: "C:\\Videos\\sample.mp4",
    });
    expect(project.id).toBe(PROJECT.id);
  });

  it("loads a project by id", async () => {
    mockedInvoke.mockResolvedValue(PROJECT);
    const project = await openProject(PROJECT.id);
    expect(mockedInvoke).toHaveBeenCalledWith("project.open", { id: PROJECT.id });
    expect(project.name).toBe("Sample");
  });

  it("lists projects", async () => {
    mockedInvoke.mockResolvedValue([PROJECT]);
    const projects = await listProjects();
    expect(mockedInvoke).toHaveBeenCalledWith("project.list");
    expect(projects).toHaveLength(1);
  });

  it("deletes a project", async () => {
    mockedInvoke.mockResolvedValue(undefined);
    await deleteProject(PROJECT.id);
    expect(mockedInvoke).toHaveBeenCalledWith("project.delete", { id: PROJECT.id });
  });
});
