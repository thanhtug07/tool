import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";

import { onJobLog } from "@/api/events";
import type { Job } from "@/api/job";
import { revealInFileManager } from "@/api/system";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { useToast } from "@/components/toast";
import { formatProcessingTime } from "@/lib/format";
import type { ArtifactPaths } from "@/api/pipeline";
import type { DerivedStageRun, PipelinePhase, PipelinePlan } from "./automation";
import {
  DEFAULT_MAX_LOG_ENTRIES,
  MAX_LOG_ENTRIES_OPTIONS,
  appendLogEntry,
  backfillFromJobs,
  buildTimeline,
  computeEta,
  formatEta,
  isAtBottom,
  toLogEntry,
  type LogEntry,
} from "./logHelpers";

const HEIGHT_KEY = "automation.livelog.height";
const COLLAPSED_KEY = "automation.livelog.collapsed";
const AUTO_SCROLL_KEY = "automation.livelog.auto-scroll";
const MAX_LOGS_KEY = "automation.livelog.max-logs";
const MIN_HEIGHT_PX = 160;
const MAX_HEIGHT_PX = 720;

function loadNumber(key: string, fallback: number): number {
  try {
    const raw = Number(localStorage.getItem(key));
    return Number.isFinite(raw) && raw > 0 ? raw : fallback;
  } catch {
    return fallback;
  }
}

function loadBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

export default function LiveLog({
  plan,
  stages,
  phase,
  overallProgress,
  jobs,
  artifacts,
  onCancel,
  onRetry,
}: {
  plan: PipelinePlan;
  stages: DerivedStageRun[];
  phase: PipelinePhase;
  overallProgress: number;
  jobs: Job[];
  artifacts: ArtifactPaths | null;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const toast = useToast();
  const [entries, setEntries] = useState<LogEntry[]>(() =>
    backfillFromJobs(plan.stages, jobs, Date.now()),
  );
  const [collapsed, setCollapsed] = useState(() =>
    phase === "running" ? false : loadBool(COLLAPSED_KEY, true),
  );
  const [autoScroll, setAutoScroll] = useState(() => loadBool(AUTO_SCROLL_KEY, true));
  const [maxLogs, setMaxLogs] = useState(() => loadNumber(MAX_LOGS_KEY, DEFAULT_MAX_LOG_ENTRIES));
  const [height, setHeight] = useState(() => loadNumber(HEIGHT_KEY, 320));
  const [showNewLogs, setShowNewLogs] = useState(false);
  const [dragging, setDragging] = useState(false);
  const consoleRef = useRef<HTMLDivElement | null>(null);
  const nextId = useRef(0);
  const backfilledRef = useRef(false);

  // Subscribe to real job:log events from the Rust core. Only lines for the
  // stages this plan owns are shown (cross-project noise is dropped).
  const planJobIds = useMemo(
    () => new Set(plan.stages.map((s) => s.jobId).filter((id): id is string => id !== null)),
    [plan.stages],
  );
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void onJobLog((event) => {
      if (cancelled) return;
      if (!planJobIds.has(event.jobId)) return;
      const id = nextId.current++;
      setEntries((current) =>
        appendLogEntry(
          current,
          toLogEntry(event, id, Date.now()),
          Math.max(1, Math.min(2000, maxLogs)),
        ),
      );
    }).then((stop) => {
      if (cancelled) stop();
      else unlisten = stop;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [planJobIds, maxLogs]);

  // Backfill stage history once per plan run (survives reloads). Runs after
  // the subscription mounts so live events win over stale rows.
  useEffect(() => {
    if (backfilledRef.current) return;
    backfilledRef.current = true;
    const seeded = backfillFromJobs(plan.stages, jobs, Date.now());
    if (seeded.length > 0) {
      setEntries((current) =>
        current.length === 0
          ? seeded
          : [...current, ...seeded].slice(-Math.max(1, Math.min(2000, maxLogs))),
      );
    }
  }, [plan.stages, jobs, maxLogs]);

  // Auto-scroll: follow the newest line while the user is at the bottom;
  // otherwise surface a "↓ New logs" pill instead of yanking the viewport.
  useEffect(() => {
    if (!autoScroll || collapsed) return;
    if (showNewLogs) return;
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, autoScroll, collapsed, showNewLogs]);

  const handleConsoleScroll = useCallback(() => {
    const el = consoleRef.current;
    setShowNewLogs(!isAtBottom(el));
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setShowNewLogs(false);
  }, []);

  const clearLog = useCallback(() => {
    setEntries([]);
    nextId.current = 0;
  }, []);

  const startDrag = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      setDragging(true);
      const startY = e.clientY;
      const startH = height;
      const onMove = (ev: PointerEvent) => {
        const next = Math.min(MAX_HEIGHT_PX, Math.max(MIN_HEIGHT_PX, startH + (startY - ev.clientY)));
        setHeight(next);
      };
      const onUp = () => {
        setDragging(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [height],
  );

  useEffect(() => {
    try {
      localStorage.setItem(HEIGHT_KEY, String(height));
    } catch {
        // no-op outside the browser shell
    }
  }, [height]);
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
        // no-op outside the browser shell
    }
  }, [collapsed]);
  useEffect(() => {
    try {
      localStorage.setItem(AUTO_SCROLL_KEY, autoScroll ? "1" : "0");
    } catch {
        // no-op outside the browser shell
    }
  }, [autoScroll]);
  useEffect(() => {
    try {
      localStorage.setItem(MAX_LOGS_KEY, String(maxLogs));
    } catch {
        // no-op outside the browser shell
    }
  }, [maxLogs]);

  const openOutput = useCallback(async () => {
    if (!artifacts?.renderedVideo) return;
    try {
      await revealInFileManager(artifacts.renderedVideo);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }, [artifacts, toast]);
  const openFolder = useCallback(async () => {
    if (!artifacts?.projectDir) return;
    try {
      await revealInFileManager(artifacts.projectDir);
    } catch (e) {
      toast.push(String(e), "error");
    }
  }, [artifacts, toast]);

  return (
    <LiveLogView
      plan={plan}
      stages={stages}
      phase={phase}
      overallProgress={overallProgress}
      jobs={jobs}
      artifacts={artifacts}
      entries={entries}
      collapsed={collapsed}
      autoScroll={autoScroll}
      maxLogs={maxLogs}
      height={height}
      showNewLogs={showNewLogs}
      dragging={dragging}
      onToggleCollapsed={() => setCollapsed((c) => !c)}
      onSetAutoScroll={setAutoScroll}
      onSetMaxLogs={setMaxLogs}
      onClear={clearLog}
      onConsoleScroll={handleConsoleScroll}
      onScrollToBottom={scrollToBottom}
      onResizeStart={startDrag}
      onCancel={onCancel}
      onRetry={onRetry}
      onOpenOutput={() => void openOutput()}
      onOpenFolder={() => void openFolder()}
    />
  );
}

export function LiveLogView({
  plan,
  stages,
  phase,
  overallProgress,
  jobs,
  artifacts,
  entries,
  collapsed,
  autoScroll,
  maxLogs,
  height,
  showNewLogs,
  dragging,
  onToggleCollapsed,
  onSetAutoScroll,
  onSetMaxLogs,
  onClear,
  onConsoleScroll,
  onScrollToBottom,
  onResizeStart,
  onCancel,
  onRetry,
  onOpenOutput,
  onOpenFolder,
}: {
  plan: PipelinePlan;
  stages: DerivedStageRun[];
  phase: PipelinePhase;
  overallProgress: number;
  jobs: Job[];
  artifacts: ArtifactPaths | null;
  entries: LogEntry[];
  collapsed: boolean;
  autoScroll: boolean;
  maxLogs: number;
  height: number;
  showNewLogs: boolean;
  dragging: boolean;
  onToggleCollapsed: () => void;
  onSetAutoScroll: (v: boolean) => void;
  onSetMaxLogs: (v: number) => void;
  onClear: () => void;
  onConsoleScroll: () => void;
  onScrollToBottom: () => void;
  onResizeStart: (e: React.PointerEvent) => void;
  onCancel: () => void;
  onRetry: () => void;
  onOpenOutput: () => void;
  onOpenFolder: () => void;
}) {
  const timeline = useMemo(() => buildTimeline(stages, jobs), [stages, jobs]);
  const runningStage = stages.find((s) => s.status === "running" || s.status === "queued");
  const currentLabel = runningStage
    ? timeline.find((t) => t.key === runningStage.key)?.label
    : null;
  const lastMessage = entries.length > 0 ? entries[entries.length - 1].message : null;

  const elapsedMs = plan.startedAt ? Date.now() - plan.startedAt : 0;
  const etaMs = phase === "running" ? computeEta(overallProgress, elapsedMs) : null;

  const failed = stages.find((s) => s.status === "failed");
  const activeRunning = phase === "running";

  return (
    <section
      data-role="live-log"
      aria-label="Automation live log"
      className="rounded-lg border border-border bg-card"
      style={dragging ? { userSelect: "none" } : undefined}
    >
      {/* Drag resize handle */}
      <div
        data-role="live-log-resize"
        onPointerDown={onResizeStart}
        className="group flex h-2 cursor-row-resize items-center justify-center"
        title="Drag to resize"
      >
        <div className="h-0.5 w-16 rounded-full bg-border transition-colors group-hover:bg-primary/50" />
      </div>

      {/* Header */}
      <div className="flex items-center justify-between gap-2 px-4 pb-2">
        <button
          data-role="live-log-toggle"
          type="button"
          onClick={onToggleCollapsed}
          className="flex items-center gap-2 text-sm font-semibold"
        >
          {collapsed ? (
            <ChevronRight className="size-4" aria-hidden="true" />
          ) : (
            <ChevronDown className="size-4" aria-hidden="true" />
          )}
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden="true" />
            Automation Live Log
          </span>
        </button>
        <div className="flex items-center gap-1">
          {activeRunning && (
            <span
              data-role="live-log-status"
              className="inline-flex items-center gap-1 rounded-full bg-emerald-400/10 px-2 py-0.5 text-[11px] font-medium text-emerald-400"
            >
              <Loader2 className="size-3 animate-spin" aria-hidden="true" /> Running
            </span>
          )}
          {phase === "succeeded" && (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-400/10 px-2 py-0.5 text-[11px] font-medium text-emerald-400">
              <Check className="size-3" aria-hidden="true" /> Completed
            </span>
          )}
          {phase === "failed" && (
            <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-400">
              <X className="size-3" aria-hidden="true" /> Failed
            </span>
          )}
          {phase === "cancelled" && (
            <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              <X className="size-3" aria-hidden="true" /> Cancelled
            </span>
          )}
          {!collapsed && (
            <>
              <label className="ml-1 flex items-center gap-1 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  data-role="auto-scroll"
                  checked={autoScroll}
                  onChange={(e) => onSetAutoScroll(e.target.checked)}
                />
                Auto-scroll
              </label>
              <select
                data-role="max-logs"
                value={maxLogs}
                onChange={(e) => onSetMaxLogs(Number(e.target.value))}
                className="rounded border border-border bg-background px-1 py-0.5 text-[11px]"
                title="Maximum visible log lines"
              >
                {MAX_LOG_ENTRIES_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n} lines
                  </option>
                ))}
              </select>
              <Button variant="ghost" size="icon" onClick={onClear} title="Clear log">
                <Trash2 className="size-3.5" aria-hidden="true" />
              </Button>
            </>
          )}
        </div>
      </div>

      {!collapsed && (
        <div className="border-t border-border px-4 py-3">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            {/* CURRENT TASK panel */}
            <div
              data-role="current-task"
              className="rounded-md border border-border bg-muted/20 p-3"
            >
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Current task
              </p>
              {activeRunning && runningStage ? (
                <>
                  <p className="mt-1 text-sm font-semibold">{currentLabel ?? runningStage.stage}</p>
                  {lastMessage && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground" title={lastMessage}>
                      {lastMessage}
                    </p>
                  )}
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      data-role="current-progress"
                      className="h-full rounded-full bg-primary transition-[width] duration-300"
                      style={{ width: `${Math.round(overallProgress * 100)}%` }}
                    />
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>
                      Overall progress{" "}
                      <span className="font-medium text-foreground" data-role="overall-pct">
                        {Math.round(overallProgress * 100)}%
                      </span>
                    </span>
                    <span>
                      Elapsed{" "}
                      <span className="tabular-nums">{formatProcessingTime(elapsedMs)}</span>
                    </span>
                    {etaMs !== null && (
                      <span>
                        ETA <span className="tabular-nums">{formatEta(etaMs)}</span>
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">
                  {phase === "succeeded"
                    ? "All stages completed."
                    : phase === "failed"
                      ? "The pipeline stopped at a failed stage."
                      : phase === "cancelled"
                        ? "The pipeline was cancelled."
                        : "Start an automation to see live progress."}
                </p>
              )}
            </div>

            {/* TIMELINE */}
            <div data-role="timeline" className="rounded-md border border-border bg-muted/20 p-3">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Stages
              </p>
              <ol className="mt-2 space-y-1">
                {timeline.map((item) => (
                  <li key={item.key} className="flex items-center gap-2 text-xs">
                    <span
                      className={cn(
                        "grid size-4 shrink-0 place-items-center rounded-full",
                        item.status === "succeeded" && "bg-emerald-400/20 text-emerald-400",
                        item.status === "running" && "bg-primary/20 text-primary",
                        item.status === "failed" && "bg-red-500/20 text-red-400",
                        item.status === "pending" && "bg-muted text-muted-foreground",
                        item.status === "cancelled" && "bg-muted text-muted-foreground",
                      )}
                    >
                      {item.status === "succeeded" ? (
                        <Check className="size-3" aria-hidden="true" />
                      ) : item.status === "running" ? (
                        <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                      ) : (
                        <Circle className="size-2" aria-hidden="true" />
                      )}
                    </span>
                    <span
                      className={cn(
                        item.status === "running" && "font-semibold text-foreground",
                        item.status === "pending" && "text-muted-foreground",
                      )}
                    >
                      {item.label}
                    </span>
                    {item.message && (
                      <span className="truncate text-muted-foreground" title={item.message}>
                        — {item.message}
                      </span>
                    )}
                    {item.finishedAt && item.startedAt && (
                      <span className="ml-auto tabular-nums text-muted-foreground">
                        {formatProcessingTime(item.finishedAt - item.startedAt)}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          </div>

          {/* CONSOLE */}
          <div className="relative mt-3">
            <div
              data-role="console"
              onScroll={onConsoleScroll}
              className="overflow-y-auto rounded-md border border-border bg-background p-2 font-mono text-[11px] leading-relaxed"
              style={{ height }}
            >
              {entries.length === 0 ? (
                <p className="text-muted-foreground">Waiting for the first job event…</p>
              ) : (
                entries.map((e) => (
                  <div key={e.id} className="flex gap-2">
                    <span className="shrink-0 tabular-nums text-muted-foreground/70">
                      {new Date(e.time).toLocaleTimeString()}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 font-semibold uppercase",
                        e.level === "info" && "text-sky-400",
                        e.level === "success" && "text-emerald-400",
                        e.level === "warn" && "text-amber-400",
                        e.level === "error" && "text-red-400",
                      )}
                    >
                      {e.level}
                    </span>
                    <span className="min-w-0 break-words">{e.message}</span>
                  </div>
                ))
              )}
            </div>
            {showNewLogs && (
              <button
                data-role="new-logs"
                type="button"
                onClick={onScrollToBottom}
                className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full border border-border bg-background px-3 py-1 text-[11px] font-medium shadow"
              >
                ↓ New logs
              </button>
            )}
          </div>

          {/* FOOTER ACTIONS */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {activeRunning && (
              <Button size="sm" variant="outline" onClick={onCancel} data-role="cancel-automation">
                <X className="size-3.5 text-red-400" aria-hidden="true" /> Cancel Automation
              </Button>
            )}
            {phase === "failed" && failed && (
              <Button size="sm" variant="outline" onClick={onRetry} data-role="retry-stage">
                <RotateCcw className="size-3.5" aria-hidden="true" /> Retry
              </Button>
            )}
            {phase === "succeeded" && artifacts && (
              <>
                <Button size="sm" variant="outline" onClick={onOpenOutput} data-role="open-output">
                  Open Output
                </Button>
                <Button size="sm" variant="ghost" onClick={onOpenFolder} data-role="open-folder">
                  Open Folder
                </Button>
              </>
            )}
          </div>

          {/* COMPLETED summary */}
          {phase === "succeeded" && plan.startedAt && (
            <div
              data-role="completed-summary"
              className="mt-3 rounded-md border border-emerald-400/20 bg-emerald-400/5 p-3"
            >
              <p className="flex items-center gap-1.5 text-sm font-semibold text-emerald-400">
                <Check className="size-4" aria-hidden="true" /> Automation completed
              </p>
              <div className="mt-1 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
                <span>
                  Total time:{" "}
                  <span className="font-medium text-foreground">
                    {formatProcessingTime(elapsedMs)}
                  </span>
                </span>
                <span>
                  Output:{" "}
                  <span className="font-medium text-foreground" data-role="output-path">
                    {artifacts?.renderedVideo ?? "not available"}
                  </span>
                </span>
              </div>
            </div>
          )}

          {/* FAILED summary */}
          {phase === "failed" && failed && (
            <div
              data-role="failed-summary"
              className="mt-3 rounded-md border border-red-500/20 bg-red-500/5 p-3"
            >
              <p className="flex items-center gap-1.5 text-sm font-semibold text-red-400">
                <X className="size-4" aria-hidden="true" /> Automation failed
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Stage:{" "}
                <span className="font-medium text-foreground">{stageLabelOf(failed.key)}</span>
              </p>
              {(failed.errorCode || failed.errorMessage) && (
                <details className="mt-1 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    {failed.errorMessage ?? failed.errorCode}
                  </summary>
                  <p className="mt-1 break-words rounded bg-muted/40 p-2 text-muted-foreground">
                    Code: {failed.errorCode ?? "unknown"}
                    {failed.errorMessage ? ` — ${failed.errorMessage}` : ""}
                  </p>
                </details>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function stageLabelOf(key: string): string {
  const labels: Record<string, string> = {
    transcribe: "Transcription",
    translate: "Translation",
    subtitle: "Subtitle generation",
    tts: "Voice generation",
    render: "Final rendering",
  };
  return labels[key] ?? key;
}
