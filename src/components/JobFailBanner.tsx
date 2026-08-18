import { useEffect, useState } from "react";

import { onJobStatus } from "@/api/events";
import { retryJob, type JobStatusEvent } from "@/api/job";

/** One failed job remembered for the banner (most recent first). */
export type JobFailure = {
  jobId: string;
  stage: string;
  code: string | null;
  message: string;
  /** Whether the details row is expanded ("view logs"). */
  expanded: boolean;
};

/** Reduce a `job:status` event into the failures list (pure, testable). */
export function reduceJobFailures(failures: JobFailure[], event: JobStatusEvent): JobFailure[] {
  if (event.status !== "failed") {
    return failures;
  }
  const failure: JobFailure = {
    jobId: event.jobId,
    stage: event.stage,
    code: event.error?.code ?? null,
    message: event.error?.message ?? "The job failed without a message.",
    expanded: false,
  };
  const rest = failures.filter((f) => f.jobId !== event.jobId);
  return [failure, ...rest].slice(0, 5);
}

import { useToast } from "@/components/toast";

/**
 * Job failure banner (TASK-030): subscribes to `job:status` and shows a
 * dismissible banner with the error code, a Retry action, and an expandable
 * "view logs" detail row.
 */
export default function JobFailBanner() {
  const toast = useToast();
  const [failures, setFailures] = useState<JobFailure[]>([]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void onJobStatus((event) => {
      if (!cancelled) {
        setFailures((current) => reduceJobFailures(current, event));
      }
    }).then((stop) => {
      if (cancelled) {
        stop();
      } else {
        unlisten = stop;
      }
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  return (
    <JobFailBannerList
      failures={failures}
      onDismiss={(jobId) => setFailures((current) => current.filter((f) => f.jobId !== jobId))}
      onRetry={(jobId) =>
        void retryJob(jobId).catch((e) => toast.push(`Retry failed: ${String(e)}`, "error"))
      }
      onToggle={(jobId) =>
        setFailures((current) =>
          current.map((f) => (f.jobId === jobId ? { ...f, expanded: !f.expanded } : f)),
        )
      }
    />
  );
}

/** Presentational banner list (exported for tests). */
export function JobFailBannerList({
  failures,
  onDismiss,
  onRetry,
  onToggle,
}: {
  failures: JobFailure[];
  onDismiss: (jobId: string) => void;
  onRetry: (jobId: string) => void;
  onToggle: (jobId: string) => void;
}) {
  if (failures.length === 0) {
    return null;
  }
  return (
    <div data-role="job-fail-banner" className="fixed top-4 right-4 z-50 flex w-96 flex-col gap-2">
      {failures.map((failure) => (
        <div
          key={failure.jobId}
          className="rounded border border-destructive/40 bg-background p-3 text-sm shadow-lg"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium text-destructive">Job failed</p>
              <p className="truncate text-xs text-muted-foreground">
                {failure.code ? `${failure.code} — ` : ""}
                {failure.message}
              </p>
            </div>
            <button
              type="button"
              data-role="job-fail-dismiss"
              aria-label="Dismiss"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => onDismiss(failure.jobId)}
            >
              ×
            </button>
          </div>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              data-role="job-fail-retry"
              className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground"
              onClick={() => onRetry(failure.jobId)}
            >
              Retry
            </button>
            <button
              type="button"
              data-role="job-fail-logs"
              className="rounded border border-border px-2 py-1 text-xs"
              onClick={() => onToggle(failure.jobId)}
            >
              {failure.expanded ? "Hide logs" : "View logs"}
            </button>
          </div>
          {failure.expanded && (
            <pre data-role="job-fail-detail" className="mt-2 max-h-32 overflow-auto text-xs">
              {failure.message}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
