import { invoke } from "@tauri-apps/api/core";

export type GlossaryEntry = {
  project_id: string;
  term: string;
  translation: string;
  updated_at: string;
};

export type CharacterEntry = {
  project_id: string;
  name: string;
  description: string;
  updated_at: string;
};

export function glossaryList(projectId: string): Promise<GlossaryEntry[]> {
  return invoke("dictionary.glossary.list", { projectId });
}

export function glossaryUpsert(
  projectId: string,
  term: string,
  translation: string,
): Promise<GlossaryEntry> {
  return invoke("dictionary.glossary.upsert", { projectId, term, translation });
}

export function glossaryDelete(projectId: string, term: string): Promise<void> {
  return invoke("dictionary.glossary.delete", { projectId, term });
}

export function glossaryFingerprint(projectId: string): Promise<string> {
  return invoke("dictionary.glossary.fingerprint", { projectId });
}

export function characterList(projectId: string): Promise<CharacterEntry[]> {
  return invoke("dictionary.character.list", { projectId });
}

export function characterUpsert(
  projectId: string,
  name: string,
  description: string,
): Promise<CharacterEntry> {
  return invoke("dictionary.character.upsert", { projectId, name, description });
}

export function characterDelete(projectId: string, name: string): Promise<void> {
  return invoke("dictionary.character.delete", { projectId, name });
}
