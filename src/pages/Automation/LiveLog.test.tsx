import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { Job } from "@/api/job";
import { LiveLogView } from "./LiveLog";
import type { DerivedStageRun, PipelinePlan } from "./automation";
import type { LogEntry } from "./logHelpers";

const PLAN: PipelinePlan = { stages: [], startedAt: 1_700_000_000_000 };

const JOB = (over: Partial<Job> = {}): Job => ({
  id: "job_1",
  project_id: "p",
  type: "transcribe",
  status: "succeeded",
  progress: 1,
  stage: "done",
  error_code: null,
  error_message: null,
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:05Z",
  finished_at: "2026-01-01T00:00:45Z",
  ...over,
});

const STAGE = (
  key: DerivedStageRun["key"],
  status: DerivedStageRun["status"],
): DerivedStageRun => ({
  key,
  jobId: "job_1",
  status,
  progress: status === "succeeded" ? 1 : status === "running" ? 0.5 : 0,
  stage: key,
  errorCode: null,
  errorMessage: null,
});

const EMPTY_HANDLERS = {
  onToggleCollapsed: () => {},
  onSetAutoScroll: () => {},
  onSetMaxLogs: () => {},
  onClear: () => {},
  onConsoleScroll: () => {},
  onScrollToBottom: () => {},
  onResizeStart: () => {},
  onCancel: () => {},
  onRetry: () => {},
  onOpenOutput: () => {},
  onOpenFolder: () => {},
};

function render(props: Partial<Parameters<typeof LiveLogView>[0]> = {}) {
  return renderToStaticMarkup(
    <LiveLogView
      plan={PLAN}
      stages={[]}
      phase="idle"
      overallProgress={0}
      jobs={[]}
      artifacts={null}
      entries={[]}
      collapsed={false}
      autoScroll
      maxLogs={500}
      height={320}
      showNewLogs={false}
      dragging={false}
      {...EMPTY_HANDLERS}
      {...props}
    />,
  );
}

describe("LiveLogView", () => {
  it("shows the running state with current task, progress and cancel", () => {
    const html = render({
      phase: "running",
      overallProgress: 0.63,
      stages: [STAGE("transcribe", "succeeded"), STAGE("translate", "running")],
      jobs: [JOB()],
      entries: [
        { id: 0, time: 0, level: "info", message: "Transcribing audio…" },
        { id: 1, time: 1, level: "info", message: "segment 81/127" },
      ],
    });
    expect(html).toContain('data-role="live-log-status"');
    expect(html).toContain("Running");
    expect(html).toContain('data-role="overall-pct"');
    expect(html).toContain("63%");
    expect(html).toContain('data-role="cancel-automation"');
    expect(html).toContain("segment 81/127");
  });

  it("renders every console line with its level", () => {
    const entries: LogEntry[] = [
      { id: 0, time: 0, level: "info", message: "started" },
      { id: 1, time: 1, level: "success", message: "complete" },
      { id: 2, time: 2, level: "error", message: "boom" },
    ];
    const html = render({ phase: "succeeded", entries });
    expect(html).toContain(">info<");
    expect(html).toContain(">success<");
    expect(html).toContain(">error<");
    expect(html).toContain("started");
    expect(html).toContain("complete");
    expect(html).toContain("boom");
  });

  it("shows the completed summary with output path", () => {
    const html = render({
      phase: "succeeded",
      plan: { ...PLAN, stages: [{ key: "render", jobId: "job_1" }] },
      stages: [STAGE("render", "succeeded")],
      jobs: [JOB({ type: "render" })],
      artifacts: { renderedVideo: "C:\\out\\final.mp4", projectDir: "C:\\proj" } as never,
    });
    expect(html).toContain('data-role="completed-summary"');
    expect(html).toContain("Automation completed");
    expect(html).toContain('data-role="output-path"');
    expect(html).toContain("final.mp4");
    expect(html).toContain('data-role="open-output"');
    expect(html).toContain('data-role="open-folder"');
  });

  it("shows the failed summary with stage and error", () => {
    const html = render({
      phase: "failed",
      stages: [
        STAGE("transcribe", "succeeded"),
        { ...STAGE("tts", "failed"), errorCode: "E_TTS_FAILED", errorMessage: "ffmpeg failed" },
      ],
    });
    expect(html).toContain('data-role="failed-summary"');
    expect(html).toContain("Automation failed");
    expect(html).toContain("Voice generation");
    expect(html).toContain("E_TTS_FAILED");
    expect(html).toContain('data-role="retry-stage"');
  });

  it("shows the cancelled state without a failed summary", () => {
    const html = render({ phase: "cancelled", stages: [STAGE("transcribe", "cancelled")] });
    expect(html).toContain("Cancelled");
    expect(html).not.toContain('data-role="failed-summary"');
    expect(html).toContain("The pipeline was cancelled.");
  });

  it("hides the body when collapsed", () => {
    const html = render({
      collapsed: true,
      phase: "running",
      entries: [{ id: 0, time: 0, level: "info", message: "x" }],
    });
    expect(html).toContain('data-role="live-log-toggle"');
    expect(html).not.toContain('data-role="console"');
  });

  it("surfaces the 'New logs' pill when the user scrolled up", () => {
    const html = render({ showNewLogs: true, phase: "running" });
    expect(html).toContain('data-role="new-logs"');
    expect(html).toContain("New logs");
  });
});
