import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowRight, ArrowUpRight, Plus, Wrench } from "lucide-react";

import type { Job } from "@/api/job";
import type { Project } from "@/api/project";
import { getApiKeyMasked, getSettings, type SettingsSnapshot } from "@/api/settings";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { StatusBadge, StatusDot, type StatusTone } from "@/components/ui/status";
import { fileBaseName, formatDateTime, formatDuration, formatProcessingTime } from "@/lib/format";
import { isToday, jobProcessingMs, jobTypeLabel, stageLabel, STATUS_TONES } from "@/lib/pipeline";
import { isTauri } from "@/lib/env";
import { useJobs } from "@/stores/jobs";
import { useWorker } from "@/stores/worker";
import type { NavKey } from "@/components/layout/Sidebar";
import { workerStateLabel } from "@/components/layout/Sidebar";

interface DashboardPageProps {
  onNavigate: (key: NavKey) => void;
  onOpenProject: (project: Project) => void;
}

/** Real pipeline checklist (worker stage strings, per pipeline_runner.rs). */
const PIPELINE_LINES: {
  type: string;
  label: string;
  subStage?: string;
}[] = [
  { type: "transcribe", label: "Extract audio", subStage: "extract-audio" },
  { type: "transcribe", label: "Speech-to-text", subStage: "transcribe" },
  { type: "translate", label: "Translate" },
  { type: "subtitle", label: "Generate subtitles" },
  { type: "render", label: "Render video" },
];

/** Stages the current build cannot run (honest — never shown as active). */
const FUTURE_LINES = ["Voice generation", "Audio mixing", "Logo removal"] as const;

export default function DashboardPage({ onNavigate, onOpenProject }: DashboardPageProps) {
  const { jobs, projects, activeJob } = useJobs();
  const { info, hardware } = useWorker();
  const [settings, setSettings] = useState<SettingsSnapshot | null>(null);
  const [geminiKey, setGeminiKey] = useState<boolean>(false);
  const [localConfigured, setLocalConfigured] = useState<boolean>(false);

  // Real provider state (masked reads only — the secret never reaches the UI).
  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    void (async () => {
      try {
        const [snapshot, geminiMasked, localMasked] = await Promise.all([
          getSettings(),
          getApiKeyMasked("gemini").catch(() => null),
          getApiKeyMasked("local").catch(() => null),
        ]);
        if (cancelled) return;
        setSettings(snapshot);
        setGeminiKey(geminiMasked !== null);
        setLocalConfigured(localMasked !== null || snapshot["api.local.base_url"].length > 0);
      } catch {
        if (!cancelled) setSettings(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const todayStats = useMemo(() => {
    const todays = jobs.filter((job) => isToday(job.created_at));
    const succeeded = todays.filter((job) => job.status === "succeeded");
    const failed = todays.filter((job) => job.status === "failed");
    const totalMs = succeeded.reduce((sum, job) => sum + jobProcessingMs(job), 0);
    return { processed: succeeded.length, failed: failed.length, totalMs };
  }, [jobs]);

  const projectName = useMemo(() => {
    const map = new Map(projects.map((p) => [p.id, p.name]));
    return (projectId: string) => map.get(projectId) ?? projectId;
  }, [projects]);

  const providerStatus = useMemo(() => {
    if (geminiKey && localConfigured) {
      return { label: "Gemini + Local LLM", tone: "ok" as StatusTone };
    }
    if (geminiKey) return { label: "Gemini key stored", tone: "ok" as StatusTone };
    if (localConfigured) return { label: "Local LLM URL set", tone: "ok" as StatusTone };
    return { label: "Mock (offline)", tone: "muted" as StatusTone };
  }, [geminiKey, localConfigured]);

  return (
    <section aria-labelledby="dashboard-heading" className="mx-auto max-w-6xl space-y-5">
      {/* Header + quick action */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="dashboard-heading" className="text-2xl font-semibold tracking-tight">
            Dashboard
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">Your workspace at a glance.</p>
        </div>
        <Button type="button" data-role="new-automation" onClick={() => onNavigate("automation")}>
          <Plus className="size-4" aria-hidden="true" /> New Automation
        </Button>
      </div>

      {/* System status */}
      <section aria-labelledby="system-status-heading">
        <h2
          id="system-status-heading"
          className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          System status
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatusCard title="Worker" tone={workerStateLabel(info?.state ?? "stopped").tone}>
            <p className="text-sm font-medium">
              {workerStateLabel(info?.state ?? "stopped").label}
            </p>
            <p className="mt-1 truncate text-xs text-muted-foreground">
              {info?.state === "ready" && info.pid != null
                ? `PID ${info.pid}${info.port != null ? ` · port ${info.port}` : ""}`
                : (info?.last_error ?? "No worker process.")}
            </p>
          </StatusCard>

          <StatusCard title="GPU" tone={hardware ? "info" : "muted"}>
            <p className="truncate text-sm font-medium" title={gpuName(hardware)}>
              {gpuName(hardware)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {hardware
                ? hardware.vram_mb != null
                  ? `${hardware.ram_mb} MB RAM · ${Math.round(hardware.vram_mb / 1024)} GB VRAM`
                  : `${hardware.ram_mb} MB RAM`
                : "Probing…"}
            </p>
          </StatusCard>

          <StatusCard title="AI providers" tone={providerStatus.tone}>
            <p className="text-sm font-medium">{providerStatus.label}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              STT: faster-whisper (local) · TTS: not in this build
            </p>
          </StatusCard>

          <StatusCard title="Storage" tone="muted">
            <p className="text-sm font-medium">
              Cache quota{" "}
              {settings
                ? `${(settings["cache.quota_bytes"] / 1024 / 1024 / 1024).toFixed(1)} GB`
                : "—"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Live free/used disk is not exposed by the backend.
            </p>
          </StatusCard>
        </div>
      </section>

      {/* Real-time processing status */}
      <section
        aria-labelledby="realtime-heading"
        className="rounded-lg border border-border bg-card p-4"
      >
        <div className="flex items-center justify-between gap-2">
          <h2 id="realtime-heading" className="text-sm font-semibold">
            Real-time processing status
          </h2>
          {activeJob && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onNavigate("automation")}
              className="text-muted-foreground"
            >
              View <ArrowRight className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
        {activeJob ? (
          <RealtimeStatus
            job={activeJob}
            jobs={jobs}
            projectName={projectName(activeJob.project_id)}
          />
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">You're all caught up.</p>
        )}
      </section>

      {/* Recent projects */}
      <section
        aria-labelledby="recent-heading"
        className="rounded-lg border border-border bg-card p-4"
      >
        <div className="flex items-center justify-between">
          <h2 id="recent-heading" className="text-sm font-semibold">
            Recent projects
          </h2>
          <Button variant="ghost" size="sm" onClick={() => onNavigate("automation")}>
            New project <Plus className="size-3.5" aria-hidden="true" />
          </Button>
        </div>
        {projects.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No projects yet — drop a video in Automation to get started.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">Video</th>
                  <th className="py-2 pr-4 font-medium">Language</th>
                  <th className="py-2 pr-4 font-medium">Duration</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Processing time</th>
                  <th className="py-2 pr-4 font-medium">Created</th>
                  <th className="py-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {projects.slice(0, 6).map((project) => {
                  const latest = latestJob(jobs, project.id);
                  return (
                    <tr key={project.id}>
                      <td className="max-w-56 py-2 pr-4">
                        <p className="truncate font-medium" title={project.source_video_path}>
                          {project.name}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {fileBaseName(project.source_video_path)}
                        </p>
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {jobLanguage(latest) ?? "—"}
                      </td>
                      <td
                        className="py-2 pr-4 text-muted-foreground"
                        title="Not stored by the pipeline"
                      >
                        —
                      </td>
                      <td className="py-2 pr-4">
                        <StatusBadge tone={statusTone(latest?.status)}>
                          {latest ? statusLabel(latest.status) : "Draft"}
                        </StatusBadge>
                      </td>
                      <td className="py-2 pr-4 tabular-nums text-muted-foreground">
                        {latest && jobProcessingMs(latest) > 0
                          ? formatProcessingTime(jobProcessingMs(latest))
                          : "—"}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {formatDateTime(project.updated_at)}
                      </td>
                      <td className="py-2">
                        <Button variant="outline" size="sm" onClick={() => onOpenProject(project)}>
                          Open <ArrowUpRight className="size-3.5" aria-hidden="true" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Processing history */}
      <section
        aria-labelledby="history-heading"
        className="rounded-lg border border-border bg-card p-4"
      >
        <div className="flex items-center justify-between gap-2">
          <h2 id="history-heading" className="text-sm font-semibold">
            Processing history
          </h2>
          <span className="text-xs text-muted-foreground">
            Today: {todayStats.processed} processed
            {todayStats.failed > 0 ? ` · ${todayStats.failed} failed` : ""}
            {todayStats.totalMs > 0 ? ` · ${formatProcessingTime(todayStats.totalMs)} total` : ""}
          </span>
        </div>
        {jobs.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">No jobs processed yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-border">
            {jobs.slice(0, 8).map((job) => (
              <li key={job.id} className="flex items-center gap-3 py-2">
                <span className="grid size-9 shrink-0 place-items-center rounded-md border border-border bg-muted text-[9px] font-semibold uppercase text-muted-foreground">
                  vid
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm" title={projectName(job.project_id)}>
                    {projectName(job.project_id)}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {jobTypeLabel(job.type)}
                    {job.stage && job.status === "running" ? ` · ${stageLabel(job.stage)}` : ""}
                    {" · "}
                    {formatDateTime(job.created_at)}
                  </p>
                </div>
                {job.status === "running" && (
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {Math.round(job.progress * 100)}%
                  </span>
                )}
                <span className={cn("text-xs font-medium", STATUS_TONES[job.status])}>
                  {statusLabel(job.status)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Quick actions */}
      <div className="flex flex-wrap gap-3">
        <Button type="button" onClick={() => onNavigate("automation")}>
          <Plus className="size-4" aria-hidden="true" /> New Automation
        </Button>
        <Button type="button" variant="outline" onClick={() => onNavigate("tools")}>
          <Wrench className="size-4" aria-hidden="true" /> Open Tools
        </Button>
        {activeJob && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => onNavigate("automation")}
            className="text-muted-foreground"
          >
            View running job <ArrowRight className="size-4" aria-hidden="true" />
          </Button>
        )}
      </div>
    </section>
  );
}

// ---- helpers ---------------------------------------------------------------

function gpuName(hardware: { gpu_name: string | null; gpu_vendor: string | null } | null): string {
  if (!hardware) return "Probing…";
  return (
    hardware.gpu_name ??
    (hardware.gpu_vendor ? hardware.gpu_vendor.toUpperCase() : "CPU (no dedicated GPU)")
  );
}

function StatusCard({
  title,
  tone,
  children,
}: {
  title: string;
  tone: StatusTone;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3.5">
      <div className="flex items-center gap-2">
        <StatusDot tone={tone} />
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </p>
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function RealtimeStatus({
  job,
  jobs,
  projectName,
}: {
  job: Job;
  jobs: Job[];
  projectName: string;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = job.started_at ? Math.max(0, now - Date.parse(job.started_at)) : 0;
  const pct = Math.round(job.progress * 100);

  return (
    <div className="mt-3 grid gap-4 lg:grid-cols-[auto_1fr]">
      <span className="grid size-24 place-items-center rounded-md border border-border bg-muted text-[10px] font-semibold uppercase text-muted-foreground">
        vid
      </span>
      <div className="min-w-0 space-y-3">
        <div>
          <p className="truncate text-sm font-medium">{projectName}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            <span className="text-foreground">{stageLabel(job.stage)}</span>
            {" · "}
            {jobTypeLabel(job.type)} job
          </p>
          <div className="mt-2 h-2 w-full max-w-xl overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            />
          </div>
          <p className="mt-1 text-xs tabular-nums text-muted-foreground">
            {pct}% · Elapsed {formatDuration(elapsed / 1000)} · Estimated remaining — backend
            reports stages, not ETA
          </p>
        </div>

        {/* Real stage checklist (derived from the active project's jobs) */}
        <PipelineChecklist jobs={jobs} projectId={job.project_id} />
      </div>
    </div>
  );
}

/** 5 real stages + honest future lines, derived from real job rows. */
function PipelineChecklist({ jobs, projectId }: { jobs: Job[]; projectId: string }) {
  const latestByType = useMemo(() => {
    const map = new Map<string, Job>();
    for (const job of jobs) {
      if (job.project_id !== projectId) continue;
      const existing = map.get(job.type);
      if (!existing || job.created_at >= existing.created_at) map.set(job.type, job);
    }
    return map;
  }, [jobs, projectId]);

  return (
    <ol className="space-y-1 text-xs">
      {PIPELINE_LINES.map(({ type, label, subStage }) => {
        const job = latestByType.get(type);
        const status = job?.status ?? "pending";
        const running = status === "running";
        // The extract line is done once the transcribe job has moved past it.
        const done =
          status === "succeeded" ||
          (running && subStage === "extract-audio" && job?.stage !== "extract-audio");
        const active = running && (subStage ? job?.stage === subStage : true);
        const failed = status === "failed";
        const queued = status === "queued";
        return (
          <li key={`${type}-${subStage ?? ""}`} className="flex items-center gap-2">
            {failed ? (
              <span className="text-red-400">✕</span>
            ) : done ? (
              <span className="text-emerald-400">✓</span>
            ) : active || queued ? (
              <span className="size-2 animate-pulse rounded-full bg-sky-400" />
            ) : (
              <span className="size-2 rounded-full bg-muted-foreground/40" />
            )}
            <span
              className={cn(
                done && "text-muted-foreground",
                (active || queued) && "font-medium text-foreground",
                failed && "text-red-400",
              )}
            >
              {label}
            </span>
          </li>
        );
      })}
      <li className="pt-1 text-muted-foreground/70" aria-hidden="true">
        <span className="mr-2">—</span>Voice, mixing &amp; logo removal arrive with later stages.
      </li>
      {FUTURE_LINES.map((label) => (
        <li key={label} className="flex items-center gap-2 opacity-50" aria-hidden="true">
          <span className="size-2 rounded-full bg-muted-foreground/40" />
          <span>{label}</span>
          <span className="ml-auto rounded-full bg-muted px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">
            later
          </span>
        </li>
      ))}
    </ol>
  );
}

function latestJob(jobs: Job[], projectId: string): Job | null {
  let latest: Job | null = null;
  for (const job of jobs) {
    if (job.project_id !== projectId) continue;
    if (latest === null || job.created_at >= latest.created_at) latest = job;
  }
  return latest;
}

export function statusLabel(status: string): string {
  switch (status) {
    case "succeeded":
      return "Completed";
    case "running":
      return "Processing";
    case "queued":
      return "Queued";
    case "cancelled":
      return "Cancelled";
    case "failed":
      return "Failed";
    default:
      return status;
  }
}

function statusTone(status: string | undefined): StatusTone {
  switch (status) {
    case "succeeded":
      return "ok";
    case "failed":
      return "bad";
    case "running":
    case "queued":
      return "info";
    case "cancelled":
      return "muted";
    default:
      return "muted";
  }
}

function jobLanguage(job: Job | null): string | null {
  if (!job) return null;
  const params = (job as Job & { params?: Record<string, unknown> }).params;
  if (!params) return null;
  const target = params["target_language"];
  if (typeof target === "string" && target) return target;
  const source = params["language"];
  if (typeof source === "string" && source) return source;
  return null;
}
