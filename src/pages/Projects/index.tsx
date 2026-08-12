import { useCallback, useEffect, useMemo, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";

import { onJobStatus } from "@/api/events";
import { exportSubtitles, exportVideo } from "@/api/export";
import { cancelJob, listJobs, retryJob, submitJob, type Job, type JobStatus } from "@/api/job";
import { getArtifactPaths, type ArtifactPaths } from "@/api/pipeline";
import { createProject, deleteProject, listProjects, type Project } from "@/api/project";
import { Button } from "@/components/ui/button";
import { useProviders } from "@/stores/providers";

/** Ordered pipeline stages (MASTER_PLAN §17.1 job types). */
export const PIPELINE_STAGES = [
  { type: "transcribe", label: "Transcribe", hint: "Extract audio + speech-to-text" },
  { type: "translate", label: "Translate", hint: "Translate the transcript" },
  { type: "subtitle", label: "Subtitles", hint: "Generate cues + ASS/SRT files" },
  { type: "render", label: "Render", hint: "Burn subtitles into the video" },
] as const;

export type StageType = (typeof PIPELINE_STAGES)[number]["type"];

export type StageOptions = {
  provider: string;
  targetLanguage: string;
};

/** Build the `job.submit` params for a stage (pure — unit tested). */
export function buildStageParams(
  stage: StageType,
  project: Project,
  options: StageOptions,
): Record<string, unknown> {
  switch (stage) {
    case "transcribe":
      return { video_path: project.source_video_path };
    case "translate":
      return { provider: options.provider, target_language: options.targetLanguage };
    case "subtitle":
    case "render":
      return {};
  }
}

/** Latest job of a given stage type, or null. */
export function latestJob(jobs: Job[], type: string): Job | null {
  let latest: Job | null = null;
  for (const job of jobs) {
    if (job.type !== type) continue;
    if (latest === null || job.created_at >= latest.created_at) latest = job;
  }
  return latest;
}

/** Whether a stage can start given the project state + job history. */
export function canRunStage(stage: StageType, jobs: Job[], project: Project): boolean {
  const index = PIPELINE_STAGES.findIndex((s) => s.type === stage);
  if (index === 0) return project.source_video_path.trim().length > 0;
  const previous = PIPELINE_STAGES[index - 1].type;
  return latestJob(jobs, previous)?.status === "succeeded";
}

export function isActive(status: JobStatus): boolean {
  return status === "queued" || status === "running";
}

export function statusLabel(status: JobStatus): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

interface ProjectsPageProps {
  onOpenProject?: (project: Project) => void;
}

export default function ProjectsPage({ onOpenProject }: ProjectsPageProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactPaths | null>(null);
  const [name, setName] = useState("");
  const [videoPath, setVideoPath] = useState("");
  // Provider Management: the provider comes from the registry — never a
  // hard-coded list. Seeded to FREE; once the registry loads, the selection
  // follows the configured default translation provider.
  const [provider, setProvider] = useState("free");
  const [targetLanguage, setTargetLanguage] = useState("zh");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { providersFor, defaultFor, providers } = useProviders();
  const providerOptions = useMemo(
    () => providersFor("translation"),
    [providersFor, providers],
  );

  // First selection: follow the configured default once the registry loads.
  useEffect(() => {
    if (providerOptions.length === 0) return;
    if (providerOptions.some((p) => p.id === provider)) return;
    const def = defaultFor("translation");
    setProvider(
      def && providerOptions.some((p) => p.id === def.id) ? def.id : providerOptions[0].id,
    );
  }, [providerOptions, provider, defaultFor]);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await listProjects());
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void refreshProjects();
    let unlisten: (() => void) | undefined;
    void onJobStatus(() => {
      // Any status change refreshes the visible job list (cheap, correct).
      setSelected((current) => {
        if (current) void refreshJobs(current.id);
        return current;
      });
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, [refreshProjects]);

  const refreshJobs = useCallback(async (projectId: string) => {
    try {
      const loaded = await listJobs(projectId);
      setJobs(loaded);
      setArtifacts(await getArtifactPaths(projectId));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  async function pickVideo() {
    try {
      const picked = await openDialog({
        multiple: false,
        filters: [{ name: "Video", extensions: ["mp4", "mkv", "mov", "avi", "webm", "m4v"] }],
      });
      if (typeof picked === "string") setVideoPath(picked);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleCreate() {
    setError(null);
    setBusy(true);
    try {
      const project = await createProject(name, videoPath);
      setProjects((prev) => [project, ...prev]);
      await selectProject(project);
      setName("");
      setVideoPath("");
      setNotice("Project created. Run Transcribe to start the pipeline.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function selectProject(project: Project) {
    setSelected(project);
    onOpenProject?.(project);
    await refreshJobs(project.id);
  }

  async function handleDelete(project: Project) {
    setError(null);
    try {
      await deleteProject(project.id);
      if (selected?.id === project.id) {
        setSelected(null);
        setJobs([]);
        setArtifacts(null);
      }
      await refreshProjects();
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRunStage(stage: StageType) {
    if (!selected) return;
    setError(null);
    try {
      const job = await submitJob(
        selected.id,
        stage,
        buildStageParams(stage, selected, { provider, targetLanguage }),
      );
      setNotice(`${PIPELINE_STAGES.find((s) => s.type === stage)?.label} started (${job.id}).`);
      await refreshJobs(selected.id);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleCancel(jobId: string) {
    setError(null);
    try {
      await cancelJob(jobId);
      if (selected) await refreshJobs(selected.id);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRetry(jobId: string) {
    setError(null);
    try {
      await retryJob(jobId);
      if (selected) await refreshJobs(selected.id);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleExportVideo() {
    if (!artifacts) return;
    setError(null);
    try {
      const targetDir = await openDialog({ directory: true, multiple: false });
      if (typeof targetDir === "string") {
        const result = await exportVideo(artifacts.renderedVideo, targetDir, { runQc: true });
        setNotice(
          `Video exported to ${result.path} (QC ${result.qc.passed ? "passed" : "failed"}).`,
        );
      }
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleExportSubtitles() {
    if (!artifacts) return;
    setError(null);
    try {
      const targetDir = await openDialog({ directory: true, multiple: false });
      if (typeof targetDir === "string") {
        const path = await exportSubtitles(artifacts.subtitleSrt, targetDir, { format: "srt" });
        setNotice(`Subtitles exported to ${path}.`);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  const renderSucceeded = latestJob(jobs, "render")?.status === "succeeded";

  return (
    <section aria-labelledby="projects-heading" className="space-y-4">
      <h1 id="projects-heading" className="text-lg font-semibold">
        Projects
      </h1>

      {error && (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground"
        >
          {notice}
        </p>
      )}

      {/* Import a video */}
      <div className="rounded-md border border-border p-4">
        <h2 className="text-sm font-semibold">New project</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Pick a video file, give the project a name, and create it.
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm">
            <span>Name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My first localization"
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            />
          </label>
          <div className="flex flex-col gap-1 text-sm">
            <span>Video file</span>
            <div className="flex gap-2">
              <input
                value={videoPath}
                onChange={(e) => setVideoPath(e.target.value)}
                placeholder="C:\Videos\sample.mp4"
                className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
                aria-label="Video file path"
              />
              <Button type="button" variant="outline" onClick={() => void pickVideo()}>
                Browse…
              </Button>
            </div>
          </div>
          <Button
            type="button"
            disabled={busy || !name.trim() || !videoPath.trim()}
            onClick={() => void handleCreate()}
          >
            Create project
          </Button>
        </div>
      </div>

      {/* Project list */}
      <div className="rounded-md border border-border p-4">
        <h2 className="text-sm font-semibold">Projects</h2>
        {projects.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No projects yet.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {projects.map((project) => (
              <li
                key={project.id}
                className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
              >
                <button
                  type="button"
                  className="text-left text-sm font-medium hover:underline"
                  onClick={() => void selectProject(project)}
                >
                  {project.name}
                </button>
                <div className="flex items-center gap-3">
                  <span
                    className="max-w-64 truncate text-xs text-muted-foreground"
                    title={project.source_video_path}
                  >
                    {project.source_video_path}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleDelete(project)}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Pipeline panel for the opened project */}
      {selected && (
        <div className="rounded-md border border-border p-4">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold">{selected.name}</h2>
            <span className="text-xs text-muted-foreground">{selected.id}</span>
          </div>
          <p
            className="mt-1 truncate text-xs text-muted-foreground"
            title={selected.source_video_path}
          >
            Source: {selected.source_video_path}
          </p>

          {/* Stage options */}
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-sm">
              <span>Provider</span>
              <select
                data-role="translation-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              >
                {providerOptions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.provider_kind === "free" ? " (local, free)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span>Target language</span>
              <input
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
                placeholder="zh"
                className="h-9 w-24 rounded-md border border-input bg-background px-3 text-sm"
              />
            </label>
          </div>

          {/* Stage buttons */}
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE_STAGES.map((stage) => {
              const runnable = canRunStage(stage.type, jobs, selected);
              const stageJob = latestJob(jobs, stage.type);
              const running = stageJob ? isActive(stageJob.status) : false;
              return (
                <div key={stage.type} className="rounded-md border border-border p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{stage.label}</span>
                    {stageJob && (
                      <span
                        className={
                          stageJob.status === "succeeded"
                            ? "text-xs font-medium text-green-600"
                            : stageJob.status === "failed" || stageJob.status === "cancelled"
                              ? "text-xs font-medium text-destructive"
                              : "text-xs text-muted-foreground"
                        }
                      >
                        {statusLabel(stageJob.status)}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{stage.hint}</p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!runnable || running}
                    onClick={() => void handleRunStage(stage.type)}
                    className="mt-2"
                  >
                    {running ? "Running…" : "Run"}
                  </Button>
                  {!runnable && !running && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {stage.type === "transcribe"
                        ? "Needs a source video."
                        : `Run ${PIPELINE_STAGES[PIPELINE_STAGES.findIndex((s) => s.type === stage.type) - 1].label} first.`}
                    </p>
                  )}
                </div>
              );
            })}
          </div>

          {/* Export */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              disabled={!renderSucceeded}
              onClick={() => void handleExportVideo()}
            >
              Export video…
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={latestJob(jobs, "subtitle")?.status !== "succeeded"}
              onClick={() => void handleExportSubtitles()}
            >
              Export subtitles…
            </Button>
            {!renderSucceeded && (
              <span className="text-xs text-muted-foreground">
                Export video is available after Render succeeds.
              </span>
            )}
          </div>

          {/* Job history with live status */}
          <div className="mt-4">
            <h3 className="text-sm font-semibold">Jobs</h3>
            {jobs.length === 0 ? (
              <p className="mt-1 text-sm text-muted-foreground">No jobs for this project yet.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {jobs.map((job) => (
                  <li key={job.id} className="rounded-md border border-border px-3 py-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">
                          {PIPELINE_STAGES.find((s) => s.type === job.type)?.label ?? job.type}
                        </span>
                        <span className="text-xs text-muted-foreground">{job.id}</span>
                        <span className="text-xs text-muted-foreground">
                          {statusLabel(job.status)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        {isActive(job.status) && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleCancel(job.id)}
                          >
                            Cancel
                          </Button>
                        )}
                        {(job.status === "failed" || job.status === "cancelled") && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleRetry(job.id)}
                          >
                            Retry
                          </Button>
                        )}
                      </div>
                    </div>
                    {job.status === "running" && (
                      <div className="mt-2">
                        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${Math.round(job.progress * 100)}%` }}
                            role="progressbar"
                            aria-valuenow={Math.round(job.progress * 100)}
                            aria-valuemin={0}
                            aria-valuemax={100}
                          />
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {Math.round(job.progress * 100)}% — {job.stage || "working…"}
                        </p>
                      </div>
                    )}
                    {job.status === "failed" && job.error_message && (
                      <p className="mt-1 text-xs text-destructive">
                        {job.error_code ? `[${job.error_code}] ` : ""}
                        {job.error_message}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
