import { useMemo } from "react";
import { ArrowUpRight, Plus } from "lucide-react";

import type { Job } from "@/api/job";
import type { Project } from "@/api/project";
import { Button } from "@/components/ui/button";
import { StatusBadge, StatusDot, type StatusTone } from "@/components/ui/status";
import { fileBaseName, formatDateTime } from "@/lib/format";
import { jobTypeLabel, stageLabel } from "@/lib/pipeline";
import { workerStateLabel } from "@/lib/nav";
import { useJobs } from "@/stores/jobs";
import { useWorker } from "@/stores/worker";

interface HomePageProps {
  project: Project | null;
  onOpenAutomation: (project: Project | null) => void;
  onOpenProcessing: (project: Project | null) => void;
  onOpenTools: () => void;
}

/**
 * HOME — compact project hub. Recent projects, processing status, system
 * status, one primary quick action. No statistics cards or control-center clutter.
 */
export default function HomePage({ onOpenAutomation, onOpenProcessing }: HomePageProps) {
  const { jobs, projects, activeJob } = useJobs();
  const { info, hardware } = useWorker();

  const projectName = useMemo(() => {
    const map = new Map(projects.map((p) => [p.id, p.name]));
    return (projectId: string) => map.get(projectId) ?? projectId;
  }, [projects]);

  const worker = workerStateLabel(info?.state ?? "stopped");
  const gpuReady = Boolean(hardware?.gpu_name || hardware?.gpu_vendor);

  return (
    <section
      aria-labelledby="home-heading"
      className="mx-auto h-full max-w-5xl overflow-y-auto px-5 py-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="home-heading" className="text-lg font-semibold tracking-tight">
          Home
        </h1>
        <Button type="button" data-role="new-automation" onClick={() => onOpenAutomation(null)}>
          <Plus className="size-4" aria-hidden="true" /> New Automation
        </Button>
      </div>

      <section
        aria-labelledby="system-status-heading"
        className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-border pb-3 text-sm"
      >
        <h2 id="system-status-heading" className="sr-only">
          System status
        </h2>
        <StatusLine tone={worker.tone} label={`Worker · ${worker.label}`} />
        <StatusLine
          tone={gpuReady ? "ok" : "muted"}
          label={`GPU · ${gpuReady ? "Available" : "CPU"}`}
        />
      </section>

      <section aria-labelledby="realtime-heading" className="mt-4">
        <h2
          id="realtime-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Processing
        </h2>
        {activeJob ? (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <StatusDot tone="info" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{projectName(activeJob.project_id)}</p>
              <p className="text-xs text-muted-foreground">
                {stageLabel(activeJob.stage)} · {jobTypeLabel(activeJob.type)} ·{" "}
                {Math.round(activeJob.progress * 100)}%
              </p>
            </div>
            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.round(activeJob.progress * 100)}%` }}
              />
            </div>
            <Button variant="ghost" size="sm" onClick={() => onOpenAutomation(null)}>
              Open
            </Button>
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">No active job.</p>
        )}
      </section>

      <section aria-labelledby="recent-heading" className="mt-5">
        <h2
          id="recent-heading"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Recent projects
        </h2>
        {projects.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No projects yet — open Automation and choose a video.
          </p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Time</th>
                  <th className="py-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {projects.slice(0, 8).map((p) => {
                  const latest = latestJob(jobs, p.id);
                  return (
                    <tr key={p.id}>
                      <td className="max-w-64 py-2 pr-3">
                        <p className="truncate font-medium" title={p.source_video_path}>
                          {p.name}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {fileBaseName(p.source_video_path)}
                        </p>
                      </td>
                      <td className="py-2 pr-3">
                        <StatusBadge tone={statusTone(latest?.status)}>
                          {latest ? statusLabel(latest.status) : "Draft"}
                        </StatusBadge>
                      </td>
                      <td className="py-2 pr-3 tabular-nums text-muted-foreground">
                        {latest ? formatDateTime(latest.created_at) : formatDateTime(p.updated_at)}
                      </td>
                      <td className="py-2">
                        <Button variant="outline" size="sm" onClick={() => onOpenAutomation(p)}>
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

      <div className="mt-5 flex flex-wrap gap-2 pb-4">
        <Button type="button" variant="outline" onClick={() => onOpenProcessing(null)}>
          Custom workflow
        </Button>
      </div>
    </section>
  );
}

function StatusLine({ tone, label }: { tone: StatusTone; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
      <StatusDot tone={tone} />
      <span className="text-foreground">{label}</span>
    </span>
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

function statusLabel(status: string): string {
  switch (status) {
    case "succeeded":
      return "Completed";
    case "running":
      return "Processing";
    case "queued":
      return "Waiting";
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
