import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { onJobStatus } from "@/api/events";
import { listAllJobs, type Job, type JobStatusEvent } from "@/api/job";
import { listProjects, type Project } from "@/api/project";

export type JobsContextValue = {
  /** All jobs across every project, most recently updated first. */
  jobs: Job[];
  projects: Project[];
  /** The latest queued/running job (the "current job"), or null. */
  activeJob: Job | null;
  /** The most recent `job:status` event (used by transient UI states). */
  lastEvent: JobStatusEvent | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
};

const JobsContext = createContext<JobsContextValue | null>(null);

const POLL_INTERVAL_MS = 3000;

/**
 * Single source of truth for job + project state (Dashboard and Automation
 * read the same data — they can never drift apart). Subscribes to `job:status`
 * events for instant updates and polls `job.list_all` / `project.list` so the
 * snapshot is complete even across restarts.
 */
export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [lastEvent, setLastEvent] = useState<JobStatusEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refreshInFlight = useRef(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshImmediate = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const [loadedJobs, loadedProjects] = await Promise.all([listAllJobs(200), listProjects()]);
      setJobs(loadedJobs);
      setProjects(loadedProjects);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      refreshInFlight.current = false;
    }
  }, []);

  /** Debounced refresh — batches rapid calls into a single IPC round-trip. */
  const refresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void refreshImmediate();
    }, 300);
  }, [refreshImmediate]);

  useEffect(() => {
    void refreshImmediate();
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [refresh, refreshImmediate]);

  // Event-driven: merge the event into the snapshot immediately, then refresh
  // so the full row (params, timestamps) catches up.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void onJobStatus((event) => {
      if (cancelled) return;
      setLastEvent(event);
      setJobs((current) => {
        const existing = current.find((job) => job.id === event.jobId);
        if (!existing) return current;
        return current.map((job) =>
          job.id === event.jobId
            ? {
                ...job,
                status: event.status,
                progress: event.progress,
                stage: event.stage,
                error_code: event.error?.code ?? null,
                error_message: event.error?.message ?? null,
              }
            : job,
        );
      });
      // Only do a full IPC refresh on terminal state transitions — the
      // optimistic merge above already handles progress/stage updates
      // in-place, avoiding a listAllJobs IPC call every500ms during
      // progress polling (the primary cause of the "sluggish" UI feel).
      if (
        event.status === "succeeded" ||
        event.status === "failed" ||
        event.status === "cancelled"
      ) {
        void refresh();
      }
    }).then((stop) => {
      if (cancelled) stop();
      else unlisten = stop;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [refresh]);

  const activeJob = useMemo(
    () => jobs.find((job) => job.status === "queued" || job.status === "running") ?? null,
    [jobs],
  );

  const value = useMemo(
    () => ({ jobs, projects, activeJob, lastEvent, loading, error, refresh }),
    [jobs, projects, activeJob, lastEvent, loading, error, refresh],
  );

  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>;
}

/** Read the shared job/project store (must be inside <JobsProvider>). */
export function useJobs(): JobsContextValue {
  const value = useContext(JobsContext);
  if (value === null) {
    throw new Error("useJobs must be used inside <JobsProvider>");
  }
  return value;
}
