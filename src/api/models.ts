import { safeInvoke } from "@/api/invoke";

/** One downloadable translation model (`models.catalog`). */
export type ModelCatalogEntry = {
  id: string;
  name: string;
  repo_id: string;
  filename: string;
  size_bytes: number;
  vram_hint_mb: number;
};

/** An installed GGUF in the app-data models dir (`models.list_local`). */
export type LocalModelInfo = {
  file_name: string;
  path: string;
  size_bytes: number;
};

/** Result of `models.download`. */
export type ModelDownloadResult = {
  path: string;
  size_bytes: number;
  cached: boolean;
};

/** `models:download-progress` event payload. */
export type ModelDownloadProgress = {
  jobId: string;
  progress: number;
  stage: string;
  message: string | null;
};

export function modelCatalog(): Promise<{ models: ModelCatalogEntry[] }> {
  return safeInvoke("models.catalog");
}

export function listLocalModels(): Promise<LocalModelInfo[]> {
  return safeInvoke("models.list_local");
}

export function downloadModel(
  repoId: string,
  filename: string,
  mirror?: string | null,
): Promise<ModelDownloadResult> {
  return safeInvoke("models.download", { repoId, filename, mirror });
}
