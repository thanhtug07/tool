import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { pickFile } from "@/api/dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import {
  Check,
  Circle,
  Copy,
  FileVideo,
  Loader2,
  Play,
  RotateCcw,
  Upload,
  Zap,
} from "lucide-react";

import { cancelJob, retryJob, submitJob } from "@/api/job";
import { toMediaUrl } from "@/api/media";
import { getArtifactPaths, type ArtifactPaths } from "@/api/pipeline";
import { createProject, type Project } from "@/api/project";
import type { ProviderView } from "@/api/provider";
import { exportVideo } from "@/api/export";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { useToast } from "@/components/toast";
import VideoPreview from "@/components/VideoPreview";
import WatermarkConfig, {
  DEFAULT_WATERMARK,
  type WatermarkConfig as WatermarkConfigType,
} from "@/components/WatermarkConfig";
import { fileBaseName, formatDuration, formatProcessingTime } from "@/lib/format";
import { useJobs } from "@/stores/jobs";
import { useProviders } from "@/stores/providers";
import { restartWorker, useWorker } from "@/stores/worker";
import type { NavKey } from "@/components/layout/Sidebar";
import type { ToolId } from "@/pages/Tools";
import {
  buildStageParams,
  checklistState,
  currentStageLabel,
  derivePhase,
  deriveStages,
  FUTURE_STAGES,
  initialPipelinePlan,
  languageLabel,
  markStageSubmitted,
  pipelineProgress,
  startPipeline,
  SOURCE_LANGUAGES,
  STAGE_CHECKLIST,
  TARGET_LANGUAGES,
  type PipelinePlan,
  type StageKey,
  type DerivedStageRun,
} from "./automation";
import LiveLog from "./LiveLog";

interface AutomationPageProps {
  project: Project | null;
  onProjectChange: (project: Project | null) => void;
  onNavigate: (key: NavKey) => void;
  onOpenTool: (tool: ToolId, projectId?: string) => void;
}

type MediaMeta = { duration: number; width: number; height: number };

export default function AutomationPage({
  project,
  onProjectChange,
  onNavigate,
  onOpenTool,
}: AutomationPageProps) {
  const toast = useToast();
  const { jobs } = useJobs();
  const { info: workerInfo } = useWorker();
  const { providersFor, defaultFor, providers } = useProviders();

  const [artifacts, setArtifacts] = useState<ArtifactPaths | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [originalMeta, setOriginalMeta] = useState<MediaMeta | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("vi");
  // Provider Management: the provider comes from the registry — never a
  // hard-coded list. Seeded to FREE; once the registry loads, the selection
  // follows the configured default translation provider.
  const [provider, setProvider] = useState("free");
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [dubAudio, setDubAudio] = useState(false);
  const [voice, setVoice] = useState("vi-VN-HoaiMyNeural");
  const [ttsEngine, setTtsEngine] = useState("edge");
  const [watermark, setWatermark] = useState<WatermarkConfigType>(DEFAULT_WATERMARK);
  const [plan, setPlan] = useState<PipelinePlan>(initialPipelinePlan);
  const [runError, setRunError] = useState<string | null>(null);
  const [workerBanner, setWorkerBanner] = useState(false);
  const [providerBanner, setProviderBanner] = useState(false);
  const [busy, setBusy] = useState(false);

  // Keep the latest options available to the (stable) submit callback.
  const optionsRef = useRef({
    sourceLanguage,
    targetLanguage,
    provider,
    burnSubtitles,
    dubAudio,
    voice,
    ttsEngine,
    watermark,
  });
  optionsRef.current = {
    sourceLanguage,
    targetLanguage,
    provider,
    burnSubtitles,
    dubAudio,
    voice,
    ttsEngine,
    watermark,
  };

  const stages: DerivedStageRun[] = useMemo(() => deriveStages(plan, jobs), [plan, jobs]);
  const phase = derivePhase(stages, plan.startedAt);
  const overallProgress = pipelineProgress(stages);

  // Translation-capable, enabled providers from the registry.
  const providerOptions = useMemo(() => providersFor("translation"), [providersFor, providers]);
  const selectedProvider = providerOptions.find((p) => p.id === provider) ?? null;

  // First selection: follow the configured default once the registry loads.
  useEffect(() => {
    if (providerOptions.length === 0) return;
    if (providerOptions.some((p) => p.id === provider)) return;
    const def = defaultFor("translation");
    setProvider(
      def && providerOptions.some((p) => p.id === def.id) ? def.id : providerOptions[0].id,
    );
  }, [providerOptions, provider, defaultFor]);

  // Follow the active project: fetch artifact paths + original video URL.
  useEffect(() => {
    if (!project) {
      setArtifacts(null);
      setVideoUrl(null);
      setOriginalMeta(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const paths = await getArtifactPaths(project.id);
        if (!cancelled) {
          setArtifacts(paths);
          setVideoUrl(toMediaUrl(project.source_video_path));
        }
      } catch (e) {
        if (!cancelled) setRunError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project]);

  // Output preview sync: once the render stage succeeds, re-read the artifact
  // paths so the completed result (and the Live Log's Open Output) point at the
  // freshly written video without an app reload.
  useEffect(() => {
    if (!project) return;
    const render = stages.find((s) => s.key === "render");
    if (!render || render.status !== "succeeded") return;
    let cancelled = false;
    void getArtifactPaths(project.id)
      .then((paths) => {
        if (!cancelled) setArtifacts(paths);
      })
      .catch(() => {
        // Artifact refresh is best-effort; the periodic job store refresh and
        // the completion view degrade gracefully when it fails.
      });
    return () => {
      cancelled = true;
    };
  }, [project, stages]);

  // ---- video import (dialog + webview drag-drop) ---------------------------

  const handleVideoPath = useCallback(
    async (path: string) => {
      if (!path.trim() || busy) return;
      setBusy(true);
      setRunError(null);
      try {
        const created = await createProject(fileBaseName(path), path);
        onProjectChange(created);
        toast.push("Video added — configure settings and press Automate.", "success");
      } catch (e) {
        setRunError(String(e));
      } finally {
        setBusy(false);
      }
    },
    [busy, onProjectChange, toast],
  );

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void (async () => {
      try {
        const stop = await getCurrentWebview().onDragDropEvent((event) => {
          if (cancelled) return;
          if (event.payload.type === "drop") {
            const path = event.payload.paths[0];
            if (path) void handleVideoPath(path);
          }
        });
        if (cancelled) stop();
        else unlisten = stop;
      } catch {
        // Drag-drop is unavailable outside the Tauri shell — the dialog
        // button remains the import path.
      }
    })();
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [handleVideoPath]);

  async function pickVideo() {
    try {
      const picked = await pickFile({
        multiple: false,
        filters: [{ name: "Video", extensions: ["mp4", "mkv", "mov", "avi", "webm", "m4v"] }],
      });
      if (typeof picked === "string") await handleVideoPath(picked);
    } catch (e) {
      setRunError(String(e));
    }
  }

  // ---- pipeline orchestration ----------------------------------------------

  const submitStage = useCallback(
    async (key: StageKey) => {
      if (!project) return;
      const params = buildStageParams(key, {
        videoPath: project.source_video_path,
        ...optionsRef.current,
      });
      try {
        const job = await submitJob(project.id, key, params);
        setPlan((current) => markStageSubmitted(current, key, job.id));
      } catch (e) {
        setRunError(String(e));
      }
    },
    [project],
  );

  // Submit the next stage only after its predecessor succeeded. The phase
  // stays `running` between stages (startedAt is set), so this fires exactly
  // once per completed predecessor.
  useEffect(() => {
    if (phase !== "running") return;
    const pendingIndex = stages.findIndex((s) => s.jobId === null);
    if (pendingIndex === -1) return;
    if (pendingIndex === 0 || stages[pendingIndex - 1].status === "succeeded") {
      void submitStage(stages[pendingIndex].key);
    }
  }, [phase, stages, submitStage]);

  async function handleAutomate() {
    setRunError(null);
    setWorkerBanner(false);
    setProviderBanner(false);
    if (!project) {
      setRunError("Drop or choose a video to automate first.");
      return;
    }
    if (workerInfo?.state !== "ready") {
      setWorkerBanner(true);
      return;
    }
    if (selectedProvider?.needs_key && !selectedProvider.api_key_configured) {
      setProviderBanner(true);
      return;
    }
    setPlan(startPipeline({ sourceLanguage, targetLanguage, provider, dubAudio }));
    void submitStage("transcribe");
    toast.push("Automation started — the pipeline runs stage by stage.", "info");
  }

  async function handleCancel() {
    const active = stages.find((s) => s.jobId && (s.status === "running" || s.status === "queued"));
    if (!active?.jobId) return;
    try {
      await cancelJob(active.jobId);
      toast.push("Cancelling the current stage…", "info");
    } catch (e) {
      setRunError(String(e));
    }
  }

  async function handleRetry() {
    const failed = stages.find((s) => s.status === "failed");
    if (!failed?.jobId) return;
    try {
      await retryJob(failed.jobId);
      toast.push("Retrying the failed stage…", "info");
    } catch (e) {
      setRunError(String(e));
    }
  }

  async function handleReprocess() {
    if (!project) return;
    setPlan(startPipeline({ sourceLanguage, targetLanguage, provider, dubAudio }));
    void submitStage("transcribe");
  }

  async function handleExport() {
    if (!artifacts) return;
    try {
      const targetDir = await pickFile({ directory: true, multiple: false });
      if (typeof targetDir !== "string") return;
      const result = await exportVideo(artifacts.renderedVideo, targetDir, { runQc: true });
      toast.push(
        `Video exported to ${result.path} — QC ${result.qc.passed ? "passed" : "failed"}.`,
        result.qc.passed ? "success" : "error",
      );
    } catch (e) {
      setRunError(String(e));
    }
  }

  async function handleCopyPath() {
    if (!artifacts) return;
    try {
      await navigator.clipboard.writeText(artifacts.renderedVideo);
      toast.push("Output path copied to clipboard.", "success");
    } catch (e) {
      setRunError(String(e));
    }
  }

  const failedStage = stages.find((s) => s.status === "failed");

  return (
    <section aria-labelledby="automation-heading" className="mx-auto max-w-[1600px] space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="automation-heading" className="text-2xl font-semibold tracking-tight">
            Automation
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Translate, subtitle and render your video automatically — one video, one click.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {project && (
            <Button variant="outline" onClick={() => onProjectChange(null)}>
              New project
            </Button>
          )}
          <Button
            type="button"
            data-role="automate-button"
            disabled={phase === "running" || busy}
            onClick={() => void handleAutomate()}
            className="px-6 py-3 text-base"
          >
            <Zap className="size-4" aria-hidden="true" /> Automate Video
          </Button>
        </div>
      </div>

      {/* Error banners */}
      {workerBanner && (
        <Banner title="Worker unavailable">
          <p className="text-sm text-muted-foreground">
            Video processing cannot start because the AI worker is unavailable.
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void restartWorker()
                .then(() => setWorkerBanner(false))
                .catch((error) => toast.push(String(error), "error"));
            }}
          >
            <RotateCcw className="size-3.5" aria-hidden="true" /> Restart Worker
          </Button>
        </Banner>
      )}
      {providerBanner && (
        <Banner title="Translation provider isn't configured">
          <p className="text-sm text-muted-foreground">
            The {selectedProvider?.name ?? "selected"} provider needs an API key. Add one in
            Settings → Providers — keys are stored in the OS credential vault, never in the
            database.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => onNavigate("settings")}>
              Configure Provider
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setProvider("mock")}>
              Use Mock instead
            </Button>
          </div>
        </Banner>
      )}
      {runError && (
        <Banner title="Something went wrong">
          <p className="text-sm text-muted-foreground">{runError}</p>
        </Banner>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_320px]">
        {/* LEFT — original video */}
        <Panel
          title="Original video"
          hint="Watch and inspect the source. It is never modified."
          actions={
            project ? (
              <Button variant="ghost" size="sm" onClick={() => void pickVideo()}>
                <FileVideo className="size-3.5" aria-hidden="true" /> Replace
              </Button>
            ) : undefined
          }
        >
          {project && videoUrl ? (
            <div className="space-y-3">
              <VideoPreview videoUrl={videoUrl} cues={[]} onMetadata={setOriginalMeta} />
              <OriginalMetadata project={project} meta={originalMeta} />
            </div>
          ) : (
            <DropZone busy={busy} onPick={() => void pickVideo()} />
          )}
        </Panel>

        {/* RIGHT — automated result */}
        <Panel title="Automated result" hint="Live processing view and final output.">
          {phase === "idle" && <EmptyResult hasProject={project !== null} />}
          {phase === "running" && (
            <ProcessingView
              stages={stages}
              overallProgress={overallProgress}
              startedAt={plan.startedAt}
              onCancel={() => void handleCancel()}
            />
          )}
          {(phase === "succeeded" || phase === "cancelled") && (
            <CompletionView
              project={project}
              artifacts={artifacts}
              phase={phase}
              plan={plan}
              onExport={() => void handleExport()}
              onCopyPath={() => void handleCopyPath()}
              onReprocess={() => void handleReprocess()}
              onEdit={() => onOpenTool("subtitles", project?.id)}
            />
          )}
          {phase === "failed" && failedStage && (
            <FailedView
              stage={failedStage}
              onRetry={() => void handleRetry()}
              onReprocess={() => void handleReprocess()}
            />
          )}
        </Panel>

        {/* RIGHT RAIL — automation settings */}
        <SettingsPanel
          project={project}
          sourceLanguage={sourceLanguage}
          onSourceLanguageChange={setSourceLanguage}
          targetLanguage={targetLanguage}
          onTargetLanguageChange={setTargetLanguage}
          provider={provider}
          onProviderChange={setProvider}
          providerOptions={providerOptions}
          burnSubtitles={burnSubtitles}
          onBurnSubtitlesChange={setBurnSubtitles}
          dubAudio={dubAudio}
          onDubAudioChange={setDubAudio}
          voice={voice}
          onVoiceChange={setVoice}
          ttsEngine={ttsEngine}
          onTtsEngineChange={setTtsEngine}
          watermark={watermark}
          onWatermarkChange={setWatermark}
          onOpenSettings={() => onNavigate("settings")}
        />
      </div>

      {/* LIVE LOG — real job events under the video workspace */}
      <LiveLog
        plan={plan}
        stages={stages}
        phase={phase}
        overallProgress={overallProgress}
        jobs={jobs}
        artifacts={artifacts}
        onCancel={() => void handleCancel()}
        onRetry={() => void handleRetry()}
      />
    </section>
  );
}

// ---- sub-components -------------------------------------------------------

function Panel({
  title,
  hint,
  actions,
  children,
}: {
  title: string;
  hint: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs text-muted-foreground">{hint}</p>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

function DropZone({ busy, onPick }: { busy: boolean; onPick: () => void }) {
  return (
    <div
      data-role="drop-zone"
      className="grid min-h-64 place-items-center rounded-md border-2 border-dashed border-border bg-muted/30 p-6 text-center"
    >
      <div className="space-y-3">
        <Upload className="mx-auto size-8 text-muted-foreground" aria-hidden="true" />
        <div>
          <p className="text-sm font-medium">Drop video here</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Drag &amp; drop your video — MP4, MKV, MOV, AVI, WebM, M4V
          </p>
        </div>
        <div className="text-xs text-muted-foreground">or</div>
        <Button type="button" data-role="choose-video" disabled={busy} onClick={onPick}>
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
          Choose Video
        </Button>
      </div>
    </div>
  );
}

function OriginalMetadata({ project, meta }: { project: Project; meta: MediaMeta | null }) {
  return (
    <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
      <Meta label="File" value={fileBaseName(project.source_video_path)} />
      <Meta label="Resolution" value={meta ? `${meta.width}×${meta.height}` : "—"} />
      <Meta label="Duration" value={meta ? formatDuration(meta.duration) : "—"} />
      <Meta label="FPS" value="—" title="FPS is not exposed by the preview element" />
    </div>
  );
}

function Meta({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <p className="uppercase tracking-wide text-muted-foreground/70">{label}</p>
      <p className="truncate text-foreground" title={value}>
        {value}
      </p>
    </div>
  );
}

function EmptyResult({ hasProject }: { hasProject: boolean }) {
  return (
    <div
      data-role="result-empty"
      className="grid min-h-64 place-items-center rounded-md border border-border bg-muted/20 p-6 text-center"
    >
      <div className="space-y-1">
        <p className="text-sm font-medium">Automated result</p>
        <p className="text-xs text-muted-foreground">
          {hasProject
            ? "Waiting for automation — press Automate Video to start the pipeline."
            : "Add a video to begin."}
        </p>
      </div>
    </div>
  );
}

function ProcessingView({
  stages,
  overallProgress,
  startedAt,
  onCancel,
}: {
  stages: DerivedStageRun[];
  overallProgress: number;
  startedAt: number | null;
  onCancel: () => void;
}) {
  const pct = Math.round(overallProgress * 100);
  return (
    <div data-role="processing" className="space-y-4">
      <div className="grid place-items-center rounded-md border border-border bg-muted/20 py-6">
        <FileVideo className="size-8 text-muted-foreground" aria-hidden="true" />
        <p className="mt-2 text-sm font-medium">{currentStageLabel(stages)}…</p>
        <p className="text-xs text-muted-foreground">Stage-based progress from the worker</p>
      </div>

      <div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
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
          {pct}% · {currentStageLabel(stages)} · Elapsed <ElapsedSince startedAt={startedAt} />
        </p>
      </div>

      <ol className="space-y-1.5 text-sm">
        {checklistState(stages).map(({ key, label, subStage, status }) => (
          <li key={`${key}-${subStage ?? ""}`} className="flex items-center gap-2">
            {status === "succeeded" ? (
              <Check className="size-4 text-emerald-400" aria-hidden="true" />
            ) : status === "running" || status === "queued" ? (
              <Loader2 className="size-4 animate-spin text-sky-400" aria-hidden="true" />
            ) : (
              <Circle className="size-4 text-muted-foreground/50" aria-hidden="true" />
            )}
            <span
              className={cn(
                status === "succeeded" && "text-muted-foreground",
                (status === "running" || status === "queued") && "font-medium text-foreground",
              )}
            >
              {label}
            </span>
          </li>
        ))}
        <li className="pt-1 text-xs text-muted-foreground/70" aria-hidden="true">
          Not in this build — voice, audio mixing and logo removal arrive with later stages.
        </li>
        {FUTURE_STAGES.map(({ key, label }) => (
          <li key={key} aria-hidden="true" className="flex items-center gap-2 text-sm opacity-50">
            <Circle className="size-4 text-muted-foreground/40" aria-hidden="true" />
            <span>{label}</span>
            <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              later
            </span>
          </li>
        ))}
      </ol>

      <Button type="button" variant="outline" size="sm" onClick={onCancel} data-role="cancel-job">
        Cancel
      </Button>
    </div>
  );
}

function ElapsedSince({ startedAt }: { startedAt: number | null }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (!startedAt) return <span>0s</span>;
  return <span className="tabular-nums">{formatProcessingTime(now - startedAt)}</span>;
}

function CompletionView({
  project,
  artifacts,
  phase,
  plan,
  onExport,
  onCopyPath,
  onReprocess,
  onEdit,
}: {
  project: Project | null;
  artifacts: ArtifactPaths | null;
  phase: "succeeded" | "cancelled";
  plan: PipelinePlan;
  onExport: () => void;
  onCopyPath: () => void;
  onReprocess: () => void;
  onEdit: () => void;
}) {
  const { providers } = useProviders();
  const [showResult, setShowResult] = useState(true);
  const resultUrl = artifacts ? toMediaUrl(artifacts.renderedVideo) : null;
  const sourceUrl = project ? toMediaUrl(project.source_video_path) : null;

  if (phase === "cancelled") {
    return (
      <div
        data-role="cancelled"
        className="space-y-3 rounded-md border border-border bg-muted/20 p-6 text-center"
      >
        <p className="text-sm font-medium">Automation cancelled</p>
        <p className="text-xs text-muted-foreground">The pipeline stopped at the current stage.</p>
        <Button size="sm" variant="outline" onClick={onReprocess}>
          <RotateCcw className="size-3.5" aria-hidden="true" /> Reprocess
        </Button>
      </div>
    );
  }

  return (
    <div data-role="completed" className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="grid size-6 place-items-center rounded-full bg-emerald-400/20">
          <Check className="size-4 text-emerald-400" aria-hidden="true" />
        </span>
        <p className="text-sm font-semibold">Automation completed</p>
        {plan.startedAt && (
          <span className="ml-auto text-xs text-muted-foreground">
            {formatProcessingTime(Date.now() - plan.startedAt)} processing
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <ToggleButton active={!showResult} onClick={() => setShowResult(false)}>
          Original
        </ToggleButton>
        <ToggleButton active={showResult} onClick={() => setShowResult(true)}>
          Result
        </ToggleButton>
      </div>

      {showResult ? (
        resultUrl ? (
          <VideoPreview videoUrl={resultUrl} cues={[]} />
        ) : (
          <p className="text-sm text-muted-foreground">Rendered video not available.</p>
        )
      ) : sourceUrl ? (
        <VideoPreview videoUrl={sourceUrl} cues={[]} />
      ) : (
        <p className="text-sm text-muted-foreground">Original video not available.</p>
      )}

      {artifacts && (
        <div className="rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs font-medium text-muted-foreground">Output</p>
          <p className="mt-0.5 truncate text-sm" title={artifacts.renderedVideo}>
            {artifacts.renderedVideo}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
        <div>
          <p className="uppercase tracking-wide text-muted-foreground/70">Source language</p>
          <p className="text-foreground">{languageLabel(plan.options?.sourceLanguage ?? "")}</p>
        </div>
        <div>
          <p className="uppercase tracking-wide text-muted-foreground/70">Target language</p>
          <p className="text-foreground">{languageLabel(plan.options?.targetLanguage ?? "vi")}</p>
        </div>
        <div>
          <p className="uppercase tracking-wide text-muted-foreground/70">Provider</p>
          <p className="text-foreground">
            {providers.find((p) => p.id === plan.options?.provider)?.name ??
              plan.options?.provider ??
              "—"}
          </p>
        </div>
        <div>
          <p className="uppercase tracking-wide text-muted-foreground/70">Processing time</p>
          <p className="text-foreground">
            {plan.startedAt ? formatProcessingTime(Date.now() - plan.startedAt) : "—"}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" data-role="export-video" onClick={onExport}>
          <Play className="size-3.5" aria-hidden="true" /> Export
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCopyPath}>
          <Copy className="size-3.5" aria-hidden="true" /> Copy path
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onReprocess}>
          <RotateCcw className="size-3.5" aria-hidden="true" /> Reprocess
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onEdit}>
          Edit subtitles
        </Button>
      </div>
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1 text-xs font-medium",
        active
          ? "border-primary bg-primary/15 text-foreground"
          : "border-border text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function FailedView({
  stage,
  onRetry,
  onReprocess,
}: {
  stage: DerivedStageRun;
  onRetry: () => void;
  onReprocess: () => void;
}) {
  const [details, setDetails] = useState(false);
  return (
    <div
      data-role="failed"
      className="space-y-3 rounded-md border border-destructive/40 bg-destructive/5 p-4"
    >
      <p className="text-sm font-semibold text-destructive">Processing failed</p>
      <p className="text-xs text-muted-foreground">
        Stage: {STAGE_CHECKLIST.find((s) => s.key === stage.key)?.label ?? stage.key}
      </p>
      <p className="text-sm">{stage.errorMessage ?? "The job failed without a message."}</p>
      {stage.errorCode && <p className="text-xs text-muted-foreground">Code: {stage.errorCode}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" data-role="retry-job" onClick={onRetry}>
          Retry
        </Button>
        <Button size="sm" variant="ghost" onClick={onReprocess}>
          Restart pipeline
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setDetails((v) => !v)}>
          {details ? "Hide details" : "Technical details"}
        </Button>
      </div>
      {details && (
        <pre className="max-h-40 overflow-auto rounded border border-border bg-black/40 p-2 text-xs">
          {stage.errorCode ? `${stage.errorCode}: ` : ""}
          {stage.errorMessage ?? ""}
        </pre>
      )}
    </div>
  );
}

function Banner({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      data-role="banner"
      className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3"
    >
      <p className="text-sm font-semibold text-destructive">{title}</p>
      {children}
    </div>
  );
}

function SettingsPanel({
  project,
  sourceLanguage,
  onSourceLanguageChange,
  targetLanguage,
  onTargetLanguageChange,
  provider,
  onProviderChange,
  providerOptions,
  burnSubtitles,
  onBurnSubtitlesChange,
  dubAudio,
  onDubAudioChange,
  voice,
  onVoiceChange,
  ttsEngine,
  onTtsEngineChange,
  watermark,
  onWatermarkChange,
  onOpenSettings,
}: {
  project: Project | null;
  sourceLanguage: string;
  onSourceLanguageChange: (v: string) => void;
  targetLanguage: string;
  onTargetLanguageChange: (v: string) => void;
  provider: string;
  onProviderChange: (v: string) => void;
  providerOptions: ProviderView[];
  burnSubtitles: boolean;
  onBurnSubtitlesChange: (v: boolean) => void;
  dubAudio: boolean;
  onDubAudioChange: (v: boolean) => void;
  voice: string;
  onVoiceChange: (v: string) => void;
  ttsEngine: string;
  onTtsEngineChange: (v: string) => void;
  watermark: WatermarkConfigType;
  onWatermarkChange: (v: WatermarkConfigType) => void;
  onOpenSettings: () => void;
}) {
  const toast = useToast();

  async function pickWatermarkImage() {
    try {
      const picked = await pickFile({
        multiple: false,
        filters: [{ name: "Image", extensions: ["png", "jpg", "jpeg", "webp"] }],
      });
      if (typeof picked === "string") {
        onWatermarkChange({ ...watermark, kind: "image", imagePath: picked });
      }
    } catch (e) {
      toast.push(String(e), "error");
    }
  }

  return (
    <section
      aria-labelledby="automation-settings-heading"
      className="h-fit rounded-lg border border-border bg-card p-4"
    >
      <h2 id="automation-settings-heading" className="text-sm font-semibold">
        Automation settings
      </h2>

      <div className="mt-3 space-y-4">
        <Setting label="Source language">
          <select
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            value={sourceLanguage}
            onChange={(e) => onSourceLanguageChange(e.target.value)}
          >
            {SOURCE_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Setting>

        <Setting label="Target language">
          <select
            className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            value={targetLanguage}
            onChange={(e) => onTargetLanguageChange(e.target.value)}
          >
            {TARGET_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Setting>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Subtitles
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked disabled /> Translate
            <span className="text-xs text-muted-foreground">(pipeline core)</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked disabled /> Generate subtitles
            <span className="text-xs text-muted-foreground">(pipeline core)</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              data-role="burn-subtitles"
              checked={burnSubtitles}
              onChange={(e) => onBurnSubtitlesChange(e.target.checked)}
            />
            Burn subtitles into video
          </label>
          <p className="text-xs text-muted-foreground">
            Subtitle style (font, size, color, position) is fixed by the pipeline per language in
            this build.
          </p>
        </div>

        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Output
          </p>
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="text-muted-foreground">Format</span>
            <span>MP4 (H.264)</span>
          </div>
          <p className="text-xs text-muted-foreground">
            Resolution and FPS always preserve the source video.
          </p>
        </div>

        <div className="space-y-1.5 rounded-md border border-border bg-muted/20 p-2.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Options
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={dubAudio}
              onChange={(e) => onDubAudioChange(e.target.checked)}
            />
            Dub audio (voice over the original audio)
            <span className="text-xs text-muted-foreground">
              ({ttsEngine === "edge" ? "edge-tts, online" : "piper, offline"})
            </span>
          </label>
          {dubAudio && (
            <div className="grid grid-cols-2 gap-2 pt-1">
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Voice
                <select
                  className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                  value={voice}
                  onChange={(e) => onVoiceChange(e.target.value)}
                >
                  <option value="vi-VN-HoaiMyNeural">Vietnamese — female</option>
                  <option value="vi-VN-NamMinhNeural">Vietnamese — male</option>
                  <option value="zh-CN-XiaoxiaoNeural">Chinese — female</option>
                  <option value="zh-CN-YunxiNeural">Chinese — male</option>
                  <option value="en-US-AriaNeural">English — female</option>
                  <option value="en-US-GuyNeural">English — male</option>
                  <option value="ja-JP-NanamiNeural">Japanese — female</option>
                  <option value="ko-KR-SunHiNeural">Korean — female</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Engine
                <select
                  className="rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground"
                  value={ttsEngine}
                  onChange={(e) => onTtsEngineChange(e.target.value)}
                >
                  <option value="edge">edge-tts (online, natural)</option>
                  <option value="piper">piper (offline)</option>
                </select>
              </label>
            </div>
          )}
          {[
            ["Preserve background music", "requires audio separation"],
            ["Remove logo / watermark", "requires OCR + inpainting"],
          ].map(([label, why]) => (
            <label key={label} className="flex items-center gap-2 text-sm opacity-60">
              <input type="checkbox" disabled />
              {label}
              <span className="text-xs text-muted-foreground">({why})</span>
            </label>
          ))}
          <p className="text-xs text-muted-foreground">
            Voice dubbing is live (edge-tts online or piper offline); logo removal needs OCR +
            inpainting (not in this build).
          </p>
        </div>

        {/* Advanced options — default closed, keeps the essential path short. */}
        <details className="rounded-md border border-border bg-background/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Advanced options
          </summary>
          <div className="space-y-4 border-t border-border p-3">
            <Setting
              label="Translation provider"
              hint={(() => {
                const sel = providerOptions.find((p) => p.id === provider);
                if (!sel) return undefined;
                if (!sel.needs_key) {
                  return sel.provider_kind === "free" ? "Local / free — no API key" : undefined;
                }
                return sel.api_key_configured
                  ? "API key configured"
                  : "No API key stored — needs configuration";
              })()}
            >
              <select
                data-role="translation-provider"
                className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
                value={provider}
                onChange={(e) => onProviderChange(e.target.value)}
              >
                {providerOptions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.provider_kind === "free" ? " (local, free)" : ""}
                  </option>
                ))}
              </select>
              {(() => {
                const sel = providerOptions.find((p) => p.id === provider);
                return sel?.needs_key && !sel.api_key_configured ? (
                  <Button size="sm" variant="outline" className="mt-1.5" onClick={onOpenSettings}>
                    Configure API key
                  </Button>
                ) : null;
              })()}
            </Setting>

            <div className="space-y-1.5">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Branding
              </p>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!project}
                  onClick={() => void pickWatermarkImage()}
                >
                  Add logo
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!project}
                  onClick={() => onWatermarkChange({ ...watermark, kind: "text", text: "Brand" })}
                >
                  Add text
                </Button>
                {watermark.kind !== "none" && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onWatermarkChange(DEFAULT_WATERMARK)}
                  >
                    Remove
                  </Button>
                )}
              </div>
              <WatermarkConfig value={watermark} onChange={onWatermarkChange} />
              <p className="text-xs text-muted-foreground">
                The watermark is burned in by the render stage (
                {watermark.kind === "none" ? "disabled" : "enabled"}).
              </p>
            </div>
          </div>
        </details>
      </div>
    </section>
  );
}

function Setting({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
