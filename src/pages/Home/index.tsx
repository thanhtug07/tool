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
      className="relative mx-auto h-full max-w-5xl overflow-y-auto px-6 py-6 bg-radial-gradient"
    >
      {/* Header section */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-border/60 bg-card/80 p-6 shadow-lg backdrop-blur-md">
        <div>
          <h1 id="home-heading" className="text-2xl font-extrabold tracking-tight text-foreground">
            AutoTranslate Dashboard
          </h1>
          <p className="text-xs font-medium text-muted-foreground mt-1">
            Automated video localization powered by AI speech-to-text, translation, and neural TTS
          </p>
        </div>
        <Button
          type="button"
          data-role="new-automation"
          onClick={() => onOpenAutomation(null)}
          className="cta-gold h-10 px-5 font-bold tracking-wide rounded-xl active:scale-[0.98] shadow-md shadow-amber-500/20"
        >
          <Plus className="size-4 stroke-[3]" aria-hidden="true" /> New Automation
        </Button>
      </div>

      {/* System Status section */}
      <section
        aria-labelledby="system-status-heading"
        className="mt-5 flex flex-wrap items-center gap-3 rounded-lg border border-border/40 bg-card/40 px-4 py-2.5 text-xs shadow-xs"
      >
        <h2 id="system-status-heading" className="sr-only">
          System status
        </h2>
        <span className="font-semibold uppercase tracking-wider text-muted-foreground text-[10px] mr-2">
          System Status:
        </span>
        <StatusLine tone={worker.tone} label={`Worker: ${worker.label}`} />
        <span className="text-border/60">|</span>
        <StatusLine
          tone={gpuReady ? "ok" : "muted"}
          label={`GPU Acceleration: ${gpuReady ? "Active" : "CPU Fallback"}`}
        />
      </section>

      {/* Realtime processing section */}
      <section aria-labelledby="realtime-heading" className="mt-6">
        <h2
          id="realtime-heading"
          className="text-xs font-bold uppercase tracking-wider text-muted-foreground/80 px-1"
        >
          Current Processing Job
        </h2>
        {activeJob ? (
          <div className="glass-card mt-2.5 flex flex-wrap items-center gap-4 rounded-xl p-4 shadow-md">
            <StatusDot tone="info" className="size-3 animate-pulse" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground">
                {projectName(activeJob.project_id)}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {stageLabel(activeJob.stage)} · {jobTypeLabel(activeJob.type)} ·{" "}
                <span className="font-medium text-foreground">
                  {Math.round(activeJob.progress * 100)}%
                </span>
              </p>
            </div>
            <div className="h-2 w-36 overflow-hidden rounded-full bg-muted/60 p-0.5 shadow-inner">
              <div
                className="h-full rounded-full bg-gradient-to-r from-amber-400 to-amber-500 transition-all duration-300 shadow-sm"
                style={{ width: `${Math.round(activeJob.progress * 100)}%` }}
              />
            </div>
            <Button variant="outline" size="sm" onClick={() => onOpenAutomation(null)}>
              View Status
            </Button>
          </div>
        ) : (
          <div className="mt-2.5 rounded-xl border border-dashed border-border/60 bg-card/20 p-4 text-center">
            <p className="text-xs text-muted-foreground">No active background jobs processing.</p>
          </div>
        )}
      </section>

      {/* Recent projects section */}
      <section aria-labelledby="recent-heading" className="mt-7">
        <div className="flex items-center justify-between px-1">
          <h2
            id="recent-heading"
            className="text-xs font-bold uppercase tracking-wider text-muted-foreground/80"
          >
            Recent projects
          </h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onOpenProcessing(null)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Custom workflow <ArrowUpRight className="size-3 ml-1" />
          </Button>
        </div>

        {projects.length === 0 ? (
          <div className="mt-3 rounded-xl border border-border/50 bg-card/30 p-8 text-center shadow-xs">
            <p className="text-sm font-medium text-foreground">No projects created yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Click &quot;New Automation&quot; above to select a video and start translating.
            </p>
          </div>
        ) : (
          <div className="glass-card mt-2.5 overflow-hidden rounded-xl border border-border/50 shadow-md">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border/60 bg-muted/30 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    <th className="py-3 px-4 font-semibold">Project Name</th>
                    <th className="py-3 px-4 font-semibold">Status</th>
                    <th className="py-3 px-4 font-semibold">Last Modified</th>
                    <th className="py-3 px-4 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40 bg-card/20">
                  {projects.slice(0, 8).map((p) => {
                    const latest = latestJob(jobs, p.id);
                    return (
                      <tr key={p.id} className="transition-colors hover:bg-muted/30">
                        <td className="max-w-64 py-3 px-4">
                          <p
                            className="truncate font-semibold text-foreground text-sm"
                            title={p.source_video_path}
                          >
                            {p.name}
                          </p>
                          <p className="truncate text-xs text-muted-foreground/80">
                            {fileBaseName(p.source_video_path)}
                          </p>
                        </td>
                        <td className="py-3 px-4">
                          <StatusBadge tone={statusTone(latest?.status)}>
                            {latest ? statusLabel(latest.status) : "Draft"}
                          </StatusBadge>
                        </td>
                        <td className="py-3 px-4 tabular-nums text-xs text-muted-foreground">
                          {latest
                            ? formatDateTime(latest.created_at)
                            : formatDateTime(p.updated_at)}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onOpenAutomation(p)}
                            className="hover:border-primary/50 hover:text-primary"
                          >
                            Open <ArrowUpRight className="size-3.5 ml-1" aria-hidden="true" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
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
