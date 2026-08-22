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

import { onJobLog, onTaskLog } from "@/api/events";
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
  isConsecutiveDuplicate,
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
  // Always start collapsed — expand only when the user asks. Keeps video +
  // primary controls visible on 1366×768 without drowning in log lines.
  const [collapsed, setCollapsed] = useState(() => loadBool(COLLAPSED_KEY, true));
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
      setEntries((current) => {
        const entry = toLogEntry(event, id, Date.now());
        if (isConsecutiveDuplicate(current, entry)) return current;
        return appendLogEntry(current, entry, Math.max(1, Math.min(2000, maxLogs)));
      });
    }).then((stop) => {
      if (cancelled) stop();
      else unlisten = stop;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [planJobIds, maxLogs]);

  // Task logs (v2 orchestrator) — same filter, prefix with task suffix
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void onTaskLog((event) => {
      if (cancelled) return;
      if (!planJobIds.has(event.jobId)) return;
      const id = nextId.current++;
      const msg = event.taskId
        ? `[${event.taskId.split(":").pop()}] ${event.message}`
        : event.message;
      setEntries((current) => {
        const entry = toLogEntry(
          {
            jobId: event.jobId,
            level: event.level as "info",
            message: msg,
          } as unknown as Parameters<typeof toLogEntry>[0],
          id,
          Date.now(),
        );
        if (isConsecutiveDuplicate(current, entry)) return current;
        return appendLogEntry(current, entry, Math.max(1, Math.min(2000, maxLogs)));
      });
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
        const next = Math.min(
          MAX_HEIGHT_PX,
          Math.max(MIN_HEIGHT_PX, startH + (startY - ev.clientY)),
        );
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

  const pct = Math.round(overallProgress * 100);
  const statusLabel =
    phase === "running"
      ? entries.length === 0
        ? "Starting"
        : "Processing"
      : phase === "succeeded"
        ? "Completed"
        : phase === "failed"
          ? "Failed"
          : phase === "cancelled"
            ? "Cancelled"
            : "Waiting";

  return (
    <section
      data-role="live-log"
      aria-label="Automation live log"
      className="shrink-0 border-t border-border bg-panel"
      style={dragging ? { userSelect: "none" } : undefined}
    >
      {!collapsed && (
        <div
          data-role="live-log-resize"
          onPointerDown={onResizeStart}
          className="group flex h-1.5 cursor-row-resize items-center justify-center"
          title="Drag to resize"
        >
          <div className="h-0.5 w-12 rounded-full bg-border transition-colors group-hover:bg-primary/50" />
        </div>
      )}

      {/* Status bar — always visible */}
      <div className="flex items-center gap-3 px-4 py-2">
        <span
          data-role="live-log-status"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
        >
          {activeRunning ? (
            <Loader2 className="size-4 animate-spin text-info" aria-hidden="true" />
          ) : phase === "succeeded" ? (
            <Check className="size-4 text-success" aria-hidden="true" />
          ) : phase === "failed" ? (
            <X className="size-4 text-destructive" aria-hidden="true" />
          ) : (
            <Circle className="size-3 text-muted-foreground" aria-hidden="true" />
          )}
          <span className="text-foreground">{statusLabel}</span>
          {(activeRunning || phase === "succeeded") && entries.length > 0 && (
            <span className="tabular-nums" data-role="overall-pct">
              {pct}%
            </span>
          )}
          {currentLabel && activeRunning && (
            <span className="hidden truncate text-muted-foreground sm:inline">
              · {currentLabel}
            </span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {!collapsed && (
            <>
              <label className="mr-1 hidden items-center gap-1 text-[11px] text-muted-foreground sm:flex">
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
                className="hidden rounded border border-border bg-background px-1 py-0.5 text-[11px] sm:block"
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
          <Button
            data-role="live-log-toggle"
            type="button"
            size="sm"
            variant="ghost"
            onClick={onToggleCollapsed}
          >
            {collapsed ? (
              <>
                Expand <ChevronRight className="size-3.5" aria-hidden="true" />
              </>
            ) : (
              <>
                Collapse <ChevronDown className="size-3.5" aria-hidden="true" />
              </>
            )}
          </Button>
        </div>
      </div>

      {!collapsed && (
        <div className="border-t border-border px-3 py-2">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div data-role="current-task" className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Current task
              </p>
              {activeRunning && runningStage ? (
                <>
                  <p className="mt-1 text-sm font-medium">{currentLabel ?? runningStage.stage}</p>
                  {lastMessage && (
                    <p
                      className="mt-0.5 truncate text-xs text-muted-foreground"
                      title={lastMessage}
                    >
                      {lastMessage}
                    </p>
                  )}
                  <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      data-role="current-progress"
                      className="h-full rounded-full bg-info transition-[width] duration-300"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    <span>
                      Elapsed{" "}
                      <span className="tabular-nums">
                        {entries.length === 0 ? "—" : formatProcessingTime(elapsedMs)}
                      </span>
                    </span>
                    {etaMs !== null && entries.length > 0 && (
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

            <div data-role="timeline" className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Stages
              </p>
              <ol className="mt-1.5 space-y-0.5">
                {timeline.map((item) => (
                  <li key={item.key} className="flex items-center gap-2.5 py-0.5 text-xs">
                    {item.status === "succeeded" ? (
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-success/15">
                        <Check className="size-3 text-success" aria-hidden="true" />
                      </span>
                    ) : item.status === "running" ? (
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-info/15">
                        <Loader2 className="size-3 animate-spin text-info" aria-hidden="true" />
                      </span>
                    ) : item.status === "failed" ? (
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-destructive/15">
                        <X className="size-3 text-destructive" aria-hidden="true" />
                      </span>
                    ) : (
                      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted">
                        <Circle className="size-2 text-muted-foreground" aria-hidden="true" />
                      </span>
                    )}
                    <span
                      className={cn(
                        item.status === "running" && "font-medium text-foreground",
                        item.status === "pending" && "text-muted-foreground",
                      )}
                    >
                      {item.label}
                    </span>
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

          <div className="relative mt-2.5">
            <div
              data-role="console"
              onScroll={onConsoleScroll}
              className="overflow-y-auto rounded-xl border border-slate-800/80 bg-slate-950/90 p-3 font-mono text-[11px] leading-relaxed shadow-inner"
              style={{ height: Math.min(height, 240) }}
            >
              {entries.length === 0 ? (
                <p className="text-slate-500 italic">Waiting for initial job log stream…</p>
              ) : (
                entries.map((e) => (
                  <div
                    key={e.id}
                    className="flex items-start gap-2.5 py-0.5 hover:bg-slate-900/60 rounded px-1"
                  >
                    <span className="shrink-0 tabular-nums text-slate-500 text-[10px]">
                      {new Date(e.time).toLocaleTimeString()}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 font-bold uppercase text-[10px] px-1.5 py-0.2 rounded ring-1",
                        e.level === "info" && "text-sky-300 bg-sky-500/10 ring-sky-500/20",
                        e.level === "success" &&
                          "text-emerald-400 bg-emerald-500/10 ring-emerald-500/20",
                        e.level === "warn" && "text-amber-400 bg-amber-500/10 ring-amber-500/20",
                        e.level === "error" &&
                          "text-rose-400 bg-rose-500/10 ring-rose-500/20 font-bold",
                      )}
                    >
                      {e.level}
                    </span>
                    <span className="min-w-0 break-words text-slate-200">{e.message}</span>
                  </div>
                ))
              )}
            </div>
            {showNewLogs && (
              <button
                data-role="new-logs"
                type="button"
                onClick={onScrollToBottom}
                className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300 backdrop-blur-md px-3.5 py-1 text-[11px] font-semibold shadow-lg hover:bg-amber-500/20"
              >
                ↓ New logs
              </button>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {activeRunning && (
              <Button size="sm" variant="outline" onClick={onCancel} data-role="cancel-automation">
                <X className="size-3.5" aria-hidden="true" /> Cancel
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

          {phase === "succeeded" && plan.startedAt && (
            <div
              data-role="completed-summary"
              className="mt-3 rounded-lg border border-success/20 bg-success/5 p-3"
            >
              <p className="text-sm font-semibold text-success">Automation completed</p>
              <p className="mt-0.5">
                Total {formatProcessingTime(elapsedMs)}
                {artifacts?.renderedVideo ? (
                  <>
                    {" · "}
                    <span data-role="output-path">{artifacts.renderedVideo}</span>
                  </>
                ) : null}
              </p>
            </div>
          )}

          {phase === "failed" && failed && (
            <div
              data-role="failed-summary"
              className="mt-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3"
            >
              <p className="text-sm font-semibold text-destructive">Automation failed</p>
              <p className="mt-0.5">
                Stage: <span className="text-foreground">{stageLabelOf(failed.key)}</span>
                {failed.errorMessage ? ` — ${failed.errorMessage}` : ""}
                {failed.errorCode ? ` (${failed.errorCode})` : ""}
              </p>
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
