import { invoke } from "@tauri-apps/api/core";

/** Canonical Project row (subset used by the UI). */
export type Project = {
  id: string;
  name: string;
  source_video_path: string;
  status: "draft" | "active" | "archived";
  created_at: string;
  updated_at: string;
};

/** Create a project from a source video path (RELEASE-P0-005 import flow). */
export function createProject(name: string, videoPath: string): Promise<Project> {
  return invoke("project.create", { name, videoPath });
}

/** Load one project by id. */
export function openProject(id: string): Promise<Project> {
  return invoke("project.open", { id });
}

/** All projects, most recently updated first. */
export function listProjects(): Promise<Project[]> {
  return invoke("project.list");
}

/** Delete a project (row + working directories). */
export function deleteProject(id: string): Promise<void> {
  return invoke("project.delete", { id });
}
