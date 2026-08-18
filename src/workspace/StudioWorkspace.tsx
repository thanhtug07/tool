import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { RotateCcw, X } from "lucide-react";

import { pickFile } from "@/api/dialog";
import { cancelJob, retryJob, submitJob } from "@/api/job";
import { probeMedia, toMediaUrl } from "@/api/media";
import { getArtifactPaths } from "@/api/pipeline";
import {
  createProject,
  deleteProject,
  findProjectBySourceVideo,
  type Project,
} from "@/api/project";
import {
  getSubtitleCues,
  replaceSubtitleCues,
  updateSubtitleCue,
  type CuePatch,
  type SubtitleCue,
  type SubtitleCueInput,
} from "@/api/subtitle";
import { exportVideo } from "@/api/export";
import { revealInFileManager } from "@/api/system";
import { getSettings } from "@/api/settings";
import { getTtsVoices } from "@/api/voices";
import { getWorkerState, type WorkerState } from "@/api/worker";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/toast";
import type { VideoPreviewHandle } from "@/components/VideoPreview";
import {
  DEFAULT_WATERMARK,
  type WatermarkConfig as WatermarkConfigType,
} from "@/components/WatermarkConfig";
import { ASS_DEFAULT_STYLE } from "@/components/subtitleOverlay";
import { fileBaseName } from "@/lib/format";
import type { NavKey } from "@/lib/nav";
import { useJobs } from "@/stores/jobs";
import { useProviders } from "@/stores/providers";
import { restartWorker, useWorker } from "@/stores/worker";
import { setStudioStatus } from "@/stores/studio";
import type { ToolId } from "@/pages/Tools";
import LiveLog from "@/pages/Automation/LiveLog";
import CustomHeader from "./CustomHeader";
import CustomToolPanel from "./CustomToolPanel";
import ResultCard from "./ResultCard";
import {
  buildStageParams,
  checklistState,
  derivePhase,
  deriveStages,
  initialPipelinePlan,
  automationPipelineSteps,
  markStageSubmitted,
  pipelineProgress,
  startPipelineWithSteps,
  type PipelinePlan,
  type StageKey,
} from "@/pages/Automation/automation";
import CenterCanvas from "./CenterCanvas";
import LeftPanel from "./LeftPanel";
import Timeline from "./Timeline";
import { diffCues, pushUndo } from "./cueHistory";
import { buildStepsFromTools, stagesForTools, type ActiveCustomTool } from "./customTools";
import {
  loadStudioCustomTools,
  loadStudioOptions,
  loadStudioPlan,
  saveStudioCustomTools,
  saveStudioOptions,
  saveStudioPlan,
} from "./session";
import {
  DEFAULT_LOGO_REMOVAL,
  type AutomationOptions,
  type LogoRemovalConfig,
  type VideoMeta,
  type WorkspaceContext,
  type WorkspaceMode,
} from "./types";

interface StudioWorkspaceProps {
  mode: WorkspaceMode;
  project: Project | null;
  onProjectChange: (project: Project | null) => void;
  onNavigate: (key: NavKey | "automation") => void;
  onOpenTool: (tool: ToolId, projectId?: string) => void;
}

/**
 * STUDIO — the one-workspace localization workstation. Video center, tools
 * left, transcript/translation/subtitles/voice right, timeline + live log
 * bottom. All processing state is REAL: the pipeline plan, per-stage jobs,
 * cues and voices all come from the backend through the shared stores.
 */
export default function StudioWorkspace({
  mode,
  project,
  onProjectChange,
  onNavigate,
  onOpenTool,
}: StudioWorkspaceProps) {
  const toast = useToast();
  const { jobs } = useJobs();
  const { info: workerInfo } = useWorker();
  const { providersFor, defaultFor, providers } = useProviders();

  // ---- project + media ------------------------------------------------------
  const [artifacts, setArtifacts] = useState<Awaited<ReturnType<typeof getArtifactPaths>> | null>(
    null,
  );
  // Whether the rendered output file actually exists on disk (the artifact
  // paths are static, so the Result pane must not attempt a missing file).
  const [resultReady, setResultReady] = useState(false);
  // Set once the persisted session for the current project has been restored
  // (plan + options) so save-effects never clobber it with defaults. Tracked
  // per project id — a boolean would let the previous project's save-effects
  // write into the new project's key during the brief switch window.
  const [hydratedProjectId, setHydratedProjectId] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [meta, setMeta] = useState<VideoMeta | null>(null);
  const [cues, setCues] = useState<Awaited<ReturnType<typeof getSubtitleCues>>>([]);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  // Undo/redo history over cue edits (text + timestamps). Snapshots of the
  // cue list are kept here and restored through the real cue backend.
  const [undoStack, setUndoStack] = useState<SubtitleCue[][]>([]);
  const [redoStack, setRedoStack] = useState<SubtitleCue[][]>([]);
  const cuesRef = useRef<SubtitleCue[]>([]);
  cuesRef.current = cues;

  // ---- automation options ----------------------------------------------------
  const [sourceLanguage, setSourceLanguage] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("vi");
  const [provider, setProvider] = useState("free");
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [dubAudio, setDubAudio] = useState(false);
  const [voice, setVoice] = useState("");
  const [ttsEngine, setTtsEngine] = useState("edge");
  // Chunked parallel pipeline (TASK_AUTOMATION_PINELINE) — the whole chain
  // runs inside one job in 30s chunks; off by default (existing behavior).
  const [chunked, setChunked] = useState(false);
  const [watermark, setWatermark] = useState<WatermarkConfigType>(DEFAULT_WATERMARK);
  const [voiceOptions, setVoiceOptions] = useState<{ value: string; label: string }[]>([]);
  const [logoRemoval, setLogoRemoval] = useState<LogoRemovalConfig>(DEFAULT_LOGO_REMOVAL);

  // ---- pipeline + transport --------------------------------------------------
  const [plan, setPlan] = useState<PipelinePlan>(initialPipelinePlan);
  const [runError, setRunError] = useState<string | null>(null);
  const [workerBanner, setWorkerBanner] = useState(false);
  const [providerBanner, setProviderBanner] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewMode, setPreviewMode] = useState<"original" | "result" | "split">("original");
  const [currentTime, setCurrentTime] = useState(0);
  const [overlay, setOverlay] = useState(ASS_DEFAULT_STYLE);
  // ---- custom tool workspace (only meaningful in `custom` mode) ----------------
  const [customTools, setCustomTools] = useState<ActiveCustomTool[]>([]);
  // Shared logo-region draft: the Xóa logo config panel (right) and the
  // rectangle drawn on the large preview (left) edit the same state.
  const [logoRegion, setLogoRegion] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  // Real processing window: captured the moment the run reaches `succeeded`.
  const finishedAtRef = useRef<number | null>(null);
  // Set once a persisted session was restored for the project — the Settings
  // seed below must not override an explicit dubbing-off choice.
  const sessionRestoredRef = useRef(false);
  // The pipeline engine consumes ordered CustomSteps; the tool workspace
  // derives them from the active tools (system-decided dependency order).
  const workflowSteps = useMemo(() => buildStepsFromTools(customTools), [customTools]);
  const workflowRunnable = useMemo(() => stagesForTools(customTools), [customTools]);

  // Applying a tool syncs its real choices into the shared options so the
  // pipeline run uses exactly what the user configured (never a stale value).
  const applyCustomTool = useCallback((tool: ActiveCustomTool) => {
    const c = tool.config;
    if (c.audioMode !== undefined) {
      // no shared option for the audio mode — carried in the step config
    }
    if (c.sourceLanguage !== undefined) setSourceLanguage(c.sourceLanguage);
    if (c.dubSourceLanguage !== undefined) setSourceLanguage(c.dubSourceLanguage);
    if (c.dubTargetLanguage !== undefined) setTargetLanguage(c.dubTargetLanguage);
    if (c.dubProvider !== undefined) setProvider(c.dubProvider);
    if (c.dubVoice !== undefined) setVoice(c.dubVoice);
    if (c.dubEngine !== undefined) setTtsEngine(c.dubEngine);
    if (c.keepBackgroundMusic !== undefined) setDubAudio(c.keepBackgroundMusic);
    if (c.translateSourceLanguage !== undefined) setSourceLanguage(c.translateSourceLanguage);
    if (c.translateTargetLanguage !== undefined) setTargetLanguage(c.translateTargetLanguage);
    if (c.translateProvider !== undefined) setProvider(c.translateProvider);
    if (c.generateDub !== undefined) setDubAudio(c.generateDub);
    if (c.overlay !== undefined) setOverlay(c.overlay);
    setCustomTools((current) => {
      const existing = current.findIndex((t) => t.id === tool.id);
      if (existing === -1) return [...current, tool];
      const copy = [...current];
      copy[existing] = tool;
      return copy;
    });
  }, []);

  // Restore the persisted session (running plan + user options) once per
  // project. Navigation away and back remounts this workspace — without this
  // the pipeline plan (component state) would be lost mid-run and the run
  // would silently stall, and the provider/voice/dub choices would reset.
  useEffect(() => {
    if (!project) return;
    const pid = project.id;
    const savedPlan = loadStudioPlan(pid);
    if (savedPlan) setPlan(savedPlan);
    const savedOptions = loadStudioOptions(pid);
    if (savedOptions) {
      setSourceLanguage(savedOptions.sourceLanguage);
      setTargetLanguage(savedOptions.targetLanguage);
      setProvider(savedOptions.provider);
      setBurnSubtitles(savedOptions.burnSubtitles);
      setDubAudio(savedOptions.dubAudio);
      setVoice(savedOptions.voice);
      // Migration: sessions saved by the old seed kept a configured voice
      // while dubbing stayed off → the Voice control wrongly showed
      // "No dubbing". A set voice means dubbing is on (the picker only
      // produces voice+off via the explicit "No dubbing" option, which
      // clears the voice).
      if (savedOptions.voice && !savedOptions.dubAudio) setDubAudio(true);
      setTtsEngine(savedOptions.ttsEngine);
      if (savedOptions.chunked !== undefined) setChunked(savedOptions.chunked);
      setWatermark(savedOptions.watermark);
      if (savedOptions.overlay) setOverlay(savedOptions.overlay);
      if (savedOptions.logoRemoval) setLogoRemoval(savedOptions.logoRemoval);
    }
    const savedTools = loadStudioCustomTools(pid);
    if (savedTools) setCustomTools(savedTools);
    else setCustomTools([]);
    setHydratedProjectId(pid);
    sessionRestoredRef.current = true;
  }, [project]);

  // Persist the active Custom tools once hydrated (same remount guarantee as
  // the automation options above).
  useEffect(() => {
    if (!project || hydratedProjectId !== project.id) return;
    saveStudioCustomTools(project.id, customTools);
  }, [project, hydratedProjectId, customTools]);

  // Persist the automation options once hydrated — a remount must restore the
  // user's exact choices (provider, dubbing, voice) instead of defaults.
  useEffect(() => {
    if (!project || hydratedProjectId !== project.id) return;
    saveStudioOptions(project.id, {
      sourceLanguage,
      targetLanguage,
      provider,
      burnSubtitles,
      dubAudio,
      voice,
      ttsEngine,
      watermark,
      overlay,
      logoRemoval,
      chunked,
    });
  }, [
    project,
    hydratedProjectId,
    sourceLanguage,
    targetLanguage,
    provider,
    burnSubtitles,
    dubAudio,
    voice,
    ttsEngine,
    watermark,
    overlay,
    logoRemoval,
    chunked,
  ]);

  // Persist the running plan so a mid-run remount resumes instead of stalling.
  // Idle plans (no run started) are skipped — they would clobber a saved
  // running plan whenever the workspace first mounts.
  useEffect(() => {
    if (!project || hydratedProjectId !== project.id) return;
    const started = plan.startedAt !== null || plan.stages.some((s) => s.jobId !== null);
    if (started) saveStudioPlan(project.id, plan);
  }, [project, hydratedProjectId, plan]);

  const originalRef = useRef<VideoPreviewHandle | null>(null);
  const resultRef = useRef<VideoPreviewHandle | null>(null);
  // Playhead drift arrives at rAF rate from two players; quantize to ~25 Hz so
  // the transport/timeline stay smooth without re-rendering the whole studio
  // at 120 fps.
  const lastTimeRef = useRef(0);
  const handleTimeChange = useCallback((t: number) => {
    if (Math.abs(t - lastTimeRef.current) >= 0.04) {
      lastTimeRef.current = t;
      setCurrentTime(t);
    }
  }, []);

  // ---- derived (real) ---------------------------------------------------------
  const stages = useMemo(() => deriveStages(plan, jobs), [plan, jobs]);
  const phase = derivePhase(stages, plan.startedAt);
  const overallProgress = pipelineProgress(stages);

  // Real processing window: reset when a run starts, snapshot on success.
  useEffect(() => {
    if (plan.startedAt) finishedAtRef.current = null;
  }, [plan.startedAt]);
  useEffect(() => {
    if (phase === "succeeded" && plan.startedAt) finishedAtRef.current = Date.now();
  }, [phase, plan.startedAt]);
  const checklist = useMemo(() => checklistState(stages), [stages]);
  const providerOptions = useMemo(() => providersFor("translation"), [providersFor, providers]);
  const selectedProvider = providerOptions.find((p) => p.id === provider) ?? null;
  // The artifact paths are static; only surface a result URL once the rendered
  // file actually exists so the Result pane never fails on a missing file.
  const resultUrl = resultReady && artifacts ? toMediaUrl(artifacts.renderedVideo) : null;

  const optionsRef = useRef({
    sourceLanguage,
    targetLanguage,
    provider,
    burnSubtitles,
    dubAudio,
    voice,
    ttsEngine,
    watermark,
    subtitleStyle: overlay,
    logoRemoval,
    chunked,
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
    subtitleStyle: overlay,
    logoRemoval,
    chunked,
  };

  // Seed provider selection from the configured default once the registry loads.
  useEffect(() => {
    if (providerOptions.length === 0) return;
    if (providerOptions.some((p) => p.id === provider)) return;
    const def = defaultFor("translation");
    setProvider(
      def && providerOptions.some((p) => p.id === def.id) ? def.id : providerOptions[0].id,
    );
  }, [providerOptions, provider, defaultFor]);

  // Seed TTS defaults from Settings → Voice (real values, never duplicated).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const snapshot = await getSettings();
        if (cancelled) return;
        setTtsEngine(snapshot["tts.engine"]);
        setVoice(snapshot["tts.voice"]);
        // A default dubbing voice in Settings means dubbing should be on by
        // default for new Automation runs — otherwise the Voice control shows
        // "No dubbing" even though a voice is configured. Never override an
        // explicit choice already restored from the session.
        if (snapshot["tts.voice"] && !sessionRestoredRef.current) setDubAudio(true);
      } catch {
        // Outside the desktop shell settings are unavailable — keep defaults.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Real TTS voices from the backend (`settings.voices`), filtered by engine.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await getTtsVoices();
        if (cancelled) return;
        const engineVoices = result.engines.find((e) => e.id === ttsEngine)?.voices ?? [];
        setVoiceOptions(engineVoices.map((v) => ({ value: v.id, label: v.label })));
        setVoice((current) => {
          if (engineVoices.some((v) => v.id === current)) return current;
          const fallback = result.defaults[ttsEngine]?.voice;
          return fallback && engineVoices.some((v) => v.id === fallback)
            ? fallback
            : (engineVoices[0]?.id ?? current);
        });
      } catch {
        // Voices are best-effort outside the working worker.
      }
    })();
    return () => {
      cancelled = true;
    };
    // Reload when the engine changes so the voice list always matches it.
  }, [ttsEngine]);

  // Follow the active project: artifacts, source URL, metadata + cues.
  useEffect(() => {
    if (!project) {
      setArtifacts(null);
      setResultReady(false);
      setVideoUrl("");
      setMeta(null);
      setCues([]);
      setSelectedCueId(null);
      setUndoStack([]);
      setRedoStack([]);
      setRunError(null);
      return;
    }
    setResultReady(false);
    let cancelled = false;
    void (async () => {
      try {
        const paths = await getArtifactPaths(project.id);
        if (!cancelled) {
          setArtifacts(paths);
          setVideoUrl(toMediaUrl(project.source_video_path));
          void probeMedia(paths.renderedVideo)
            .then(() => {
              if (!cancelled) setResultReady(true);
            })
            .catch(() => {
              if (!cancelled) setResultReady(false);
            });
        }
      } catch (e) {
        if (!cancelled) setRunError(String(e));
      }
      try {
        const probe = await probeMedia(project.source_video_path);
        if (!cancelled) setMeta(probe);
      } catch (e) {
        if (!cancelled) setRunError(`Không thể đọc video: ${String(e)}`);
      }
      try {
        const loaded = await getSubtitleCues(project.id);
        if (!cancelled) {
          setCues(loaded);
          setSelectedCueId(null);
          // A different project starts with a clean editing history.
          setUndoStack([]);
          setRedoStack([]);
        }
      } catch {
        // Cues appear once the pipeline has run.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [project]);

  // Output preview sync: after render succeeds, re-read artifact paths so the
  // Result pane loads the freshly written video without an app reload.
  useEffect(() => {
    if (!project) return;
    const render = stages.find((s) => s.key === "render");
    if (!render || render.status !== "succeeded") return;
    let cancelled = false;
    void getArtifactPaths(project.id)
      .then((paths) => {
        if (!cancelled) setArtifacts(paths);
        return paths;
      })
      .then((paths) => {
        if (cancelled) return;
        return probeMedia(paths.renderedVideo)
          .then(() => {
            if (!cancelled) setResultReady(true);
          })
          .catch(() => {
            if (!cancelled) setResultReady(false);
          });
      })
      .catch(() => {
        // Best-effort; the periodic job refresh degrades gracefully.
      });
    return () => {
      cancelled = true;
    };
  }, [project, stages]);

  // ---- video import (dialog + webview drag-drop) -----------------------------

  const handleVideoPath = useCallback(
    async (path: string) => {
      if (!path.trim() || busy) return;
      setBusy(true);
      setRunError(null);
      try {
        const existing = await findProjectBySourceVideo(path);
        if (existing) {
          onProjectChange(existing);
          toast.push("Video already has a project — opened the existing one.", "info");
          return;
        }
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
        // Drag-drop is unavailable outside the Tauri shell.
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

  // ---- players + cues ---------------------------------------------------------

  const seek = useCallback((time: number) => {
    originalRef.current?.seekTo(time);
    resultRef.current?.seekTo(time);
    setCurrentTime(time);
  }, []);

  const refreshCues = useCallback(async () => {
    if (!project) return;
    try {
      const loaded = await getSubtitleCues(project.id);
      setCues(loaded);
    } catch {
      // Cue refresh is best-effort.
    }
  }, [project]);

  // The subtitle stage regenerates the cue table (timing-matched merge keeps
  // user edits). Refresh the editor's cue state once it succeeds so a later
  // edit/delete never operates on stale pre-run rows.
  const subtitleStageDone = stages.some((s) => s.key === "subtitle" && s.status === "succeeded");
  useEffect(() => {
    if (!project || !subtitleStageDone) return;
    void refreshCues();
  }, [project, subtitleStageDone, refreshCues]);

  // A cue edit records the pre-edit snapshot on the undo stack (any pending
  // redo branch is discarded) and then persists through the real backend.
  const updateCue = useCallback(
    async (id: string, patch: CuePatch) => {
      setUndoStack((stack) => pushUndo(stack, cuesRef.current));
      setRedoStack([]);
      await updateSubtitleCue(id, patch);
      await refreshCues();
    },
    [refreshCues],
  );

  // Delete one cue: snapshot for undo, then replace the project's cue set with
  // the remaining rows (ids are stable, so the backend keeps their numbers).
  const removeCue = useCallback(
    async (id: string) => {
      const remaining = cuesRef.current.filter((c) => c.id !== id);
      setUndoStack((stack) => pushUndo(stack, cuesRef.current));
      setRedoStack([]);
      if (remaining.length === 0 || !project) {
        await refreshCues();
        return;
      }
      const inputs: SubtitleCueInput[] = remaining.map((c) => ({
        cue_number: c.cue_number,
        start: c.start,
        end: c.end,
        text: c.text,
        speaker: c.speaker,
        source_text: c.source_text,
      }));
      try {
        await replaceSubtitleCues(project.id, inputs);
        await refreshCues();
        setSelectedCueId(null);
      } catch {
        // Undo stack was already pushed; a failed replace leaves the DB intact
        // and the next refresh shows the truth.
        await refreshCues();
      }
    },
    [project, refreshCues],
  );

  // Restore a snapshot by applying per-cue diffs (cue ids are stable, so
  // selection and timeline references survive the restore).
  const restoreSnapshot = useCallback(
    async (target: SubtitleCue[]) => {
      const patches = diffCues(cuesRef.current, target);
      for (const { id, patch } of patches) {
        await updateSubtitleCue(id, patch);
      }
      await refreshCues();
    },
    [refreshCues],
  );

  const undo = useCallback(() => {
    const target = undoStack[undoStack.length - 1];
    if (!target) return;
    setUndoStack((stack) => stack.slice(0, -1));
    setRedoStack((stack) => [...stack, cuesRef.current]);
    void restoreSnapshot(target);
  }, [undoStack, restoreSnapshot]);

  const redo = useCallback(() => {
    const target = redoStack[redoStack.length - 1];
    if (!target) return;
    setRedoStack((stack) => stack.slice(0, -1));
    setUndoStack((stack) => pushUndo(stack, cuesRef.current));
    void restoreSnapshot(target);
  }, [redoStack, restoreSnapshot]);

  // Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z / Ctrl/Cmd+Y redo — but never while
  // the focus is inside a text field (the browser's native text undo must
  // keep working there).
  const undoRef = useRef(undo);
  const redoRef = useRef(redo);
  undoRef.current = undo;
  redoRef.current = redo;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) redoRef.current();
        else undoRef.current();
      } else if (key === "y") {
        event.preventDefault();
        redoRef.current();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Dragging the caption in the preview reports frame fractions; the overlay
  // switches to `custom` and the render stage burns the text at that spot.
  const handleCaptionPosition = useCallback((x: number, y: number) => {
    setOverlay((current) => ({ ...current, position: "custom", customX: x, customY: y }));
  }, []);

  // Real ffprobe diagnosis for the "cannot open video" error (TASK video fix).
  const diagnose = useCallback(async (): Promise<string | null> => {
    if (!project) return null;
    try {
      const probe = await probeMedia(project.source_video_path);
      return `ffprobe: ${probe.container ?? "unknown"} · ${probe.width}×${probe.height} · ${
        probe.videoCodec ?? "unknown codec"
      } — ${project.source_video_path}`;
    } catch (e) {
      return `ffprobe failed: ${String(e)}`;
    }
  }, [project]);

  // ---- pipeline orchestration (same real flow as the Automation page) ----------

  const submittingRef = useRef<Set<StageKey>>(new Set());
  const submitFailedRef = useRef<Set<StageKey>>(new Set());

  const submitStage = useCallback(
    async (key: StageKey) => {
      if (!project) return;
      if (submittingRef.current.has(key)) return;
      submittingRef.current.add(key);
      const step = workflowSteps.find((s) => s.id === key);
      // The automation toolbar's logo removal is a first-class option (not a
      // custom step), so its region feeds the delogo job params directly.
      const isAutomationLogo = mode === "automation" && key === "logo";
      // Automation and Custom both use the provider chosen in the workspace
      // (Automation bar / tool config). Settings only seeds the default.
      const params = buildStageParams(key, {
        videoPath: project.source_video_path,
        ...optionsRef.current,
        provider,
        stepConfig: isAutomationLogo ? optionsRef.current.logoRemoval : step?.config,
        // The render stage must know the pre-render stages that ran so it
        // picks up their artifacts (logo_removed.mp4) as the encode input.
        enabledStages:
          mode === "custom"
            ? workflowRunnable
            : optionsRef.current.logoRemoval.enabled
              ? ["logo"]
              : undefined,
      });
      try {
        const job = await submitJob(project.id, key, params);
        setPlan((current) => markStageSubmitted(current, key, job.id));
      } catch (e) {
        submitFailedRef.current.add(key);
        setRunError(String(e));
      } finally {
        submittingRef.current.delete(key);
      }
    },
    [project, mode, workflowRunnable, workflowSteps, provider],
  );

  // Submit the next stage only after its predecessor succeeded.
  useEffect(() => {
    if (phase !== "running") return;
    const pendingIndex = stages.findIndex((s) => s.jobId === null);
    if (pendingIndex === -1) return;
    if (submitFailedRef.current.has(stages[pendingIndex].key)) return;
    if (pendingIndex === 0 || stages[pendingIndex - 1].status === "succeeded") {
      void submitStage(stages[pendingIndex].key);
    }
  }, [phase, stages, submitStage]);

  const ensureWorkerReady = useCallback(async (): Promise<boolean> => {
    const deadline = Date.now() + 15_000;
    const restartIfNeeded = async (state: WorkerState): Promise<boolean> => {
      if (state === "ready") return true;
      if (state === "failed" || state === "stopped") {
        try {
          const info = await restartWorker();
          return info.state === "ready";
        } catch {
          return false;
        }
      }
      return false;
    };
    try {
      let info = await getWorkerState();
      if (await restartIfNeeded(info.state)) return true;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 500));
        info = await getWorkerState();
        if (await restartIfNeeded(info.state)) return true;
      }
    } catch {
      return false;
    }
    return false;
  }, []);

  async function handleAutomate() {
    setRunError(null);
    setWorkerBanner(false);
    setProviderBanner(false);
    if (!project) {
      setRunError("Drop or choose a video to automate first.");
      return;
    }
    if (!(await ensureWorkerReady())) {
      setWorkerBanner(true);
      return;
    }
    if (selectedProvider?.needs_key && !selectedProvider.api_key_configured) {
      setProviderBanner(true);
      return;
    }
    if (mode === "custom" && workflowRunnable.length === 0) {
      setRunError("Choose at least one tool to run.");
      return;
    }
    const steps =
      mode === "custom"
        ? [...workflowRunnable]
        : automationPipelineSteps(dubAudio, logoRemoval.enabled, chunked);
    setPlan(
      startPipelineWithSteps(steps, {
        sourceLanguage,
        targetLanguage,
        provider,
        dubAudio,
      }),
    );
    submitFailedRef.current = new Set();
    // Submit the FIRST stage of this plan — Custom tools may not start with
    // transcribe (e.g. a bare audio/logo tool must not run STT first).
    const first = steps[0];
    if (first) void submitStage(first);
    toast.push(
      mode === "custom"
        ? "Tools started — the pipeline runs their stages in dependency order."
        : chunked
          ? "Chunked pipeline started — parallel 30s chunks, then final encode."
          : "Automation started — the pipeline runs stage by stage.",
      "info",
    );
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
    if (mode === "custom" && workflowRunnable.length === 0) {
      setRunError("Choose at least one tool to run.");
      return;
    }
    const steps =
      mode === "custom"
        ? [...workflowRunnable]
        : automationPipelineSteps(dubAudio, logoRemoval.enabled, chunked);
    setPlan(startPipelineWithSteps(steps, { sourceLanguage, targetLanguage, provider, dubAudio }));
    submitFailedRef.current = new Set();
    const first = steps[0];
    if (first) void submitStage(first);
  }

  async function handleExport() {
    if (!artifacts) {
      toast.push("Output artifacts are not ready yet.", "error");
      return;
    }
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

  /** Show the rendered output right in the Result preview tab and play it. */
  const handlePreviewResult = useCallback(() => {
    if (!resultReady || !artifacts) return;
    setPreviewMode("result");
    // The Result pane mounts after the mode switch commits — play once the
    // video element is in the DOM (a plain delay, not fake processing).
    window.setTimeout(() => resultRef.current?.play(), 120);
  }, [resultReady, artifacts]);

  /** [+ Action] → Open Output Folder: reveal the project dir in Explorer. */
  async function handleOpenOutputFolder() {
    if (!artifacts) {
      toast.push("Output folder is not ready yet.", "error");
      return;
    }
    try {
      await revealInFileManager(artifacts.projectDir);
    } catch (e) {
      setRunError(String(e));
    }
  }

  /** [+ Action] → Clear Project: delete the project row + working dirs. */
  async function handleClearProject() {
    if (!project) return;
    try {
      await deleteProject(project.id);
      onProjectChange(null);
      toast.push("Project cleared.", "success");
    } catch (e) {
      setRunError(String(e));
    }
  }

  // ---- sync the global TopBar (Export / Processing % / Undo / Redo) -----------
  const canExport = phase === "succeeded" && Boolean(artifacts?.renderedVideo);
  const canUndo = undoStack.length > 0;
  const canRedo = redoStack.length > 0;
  useEffect(() => {
    setStudioStatus({
      phase,
      overallProgress,
      canExport,
      export: canExport ? () => void handleExport() : null,
      cancel: phase === "running" ? () => void handleCancel() : null,
      undo: canUndo ? () => void undo() : null,
      redo: canRedo ? () => void redo() : null,
    });
  }, [phase, overallProgress, canExport, canUndo, canRedo, undo, redo]);

  // ---- assemble the context for every panel -----------------------------------
  const options: AutomationOptions = {
    sourceLanguage,
    setSourceLanguage,
    targetLanguage,
    setTargetLanguage,
    provider,
    setProvider,
    providerOptions,
    selectedProvider,
    burnSubtitles,
    setBurnSubtitles,
    dubAudio,
    setDubAudio,
    voice,
    setVoice,
    ttsEngine,
    setTtsEngine,
    watermark,
    setWatermark,
    voiceOptions,
    logoRemoval,
    setLogoRemoval,
    chunked,
    setChunked,
  };

  const ctx: WorkspaceContext = {
    project,
    busy,
    phase,
    stages,
    checklist,
    overallProgress,
    startedAt: plan.startedAt,
    meta,
    artifacts,
    videoUrl,
    resultUrl,
    options,
    overlay,
    setOverlay,
    actions: {
      pickVideo: () => void pickVideo(),
      automate: () => void handleAutomate(),
      cancel: () => void handleCancel(),
      retry: () => void handleRetry(),
      reprocess: () => void handleReprocess(),
      export: () => void handleExport(),
      copyPath: () => void handleCopyPath(),
      openOutputFolder: () => void handleOpenOutputFolder(),
      openProviderSettings: () => onNavigate("settings"),
      openTool: (tool, projectId) => onOpenTool(tool, projectId),
    },
    workflow: {
      steps: workflowSteps,
      // The tool workspace owns these now — pipeline order is system-decided.
      toggle: () => {},
      move: () => {},
      add: () => {},
      updateConfig: () => {},
      addable: [],
      runnable: workflowRunnable,
    },
    customTools: {
      tools: customTools,
      apply: applyCustomTool,
      remove: (id) => setCustomTools((current) => current.filter((t) => t.id !== id)),
      reset: () => setCustomTools([]),
      runnable: workflowRunnable,
    },
    logoRegion: { region: logoRegion, setRegion: setLogoRegion },
    cues: {
      cues,
      selectedId: selectedCueId,
      select: setSelectedCueId,
      seek,
      update: updateCue,
      remove: removeCue,
      undo: () => void undo(),
      redo: () => void redo(),
      canUndo,
      canRedo,
    },
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-role="studio-workspace">
      {/* Banners (real errors only) */}
      {(workerBanner || providerBanner || runError) && (
        <div className="space-y-2 border-b border-border bg-panel px-3 py-2">
          {workerBanner && (
            <Banner title="Worker unavailable">
              <p className="text-xs text-muted-foreground">
                Video processing cannot start because the AI worker is unavailable.
              </p>
              {workerInfo?.last_error && (
                <p className="text-xs text-muted-foreground">{workerInfo.last_error}</p>
              )}
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
              <p className="text-xs text-muted-foreground">
                The {selectedProvider?.name ?? "selected"} provider needs an API key. Add one in
                Settings → Providers — keys live in the OS credential vault.
              </p>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => onNavigate("settings")}>
                  Configure Provider
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setProvider("mock");
                    setProviderBanner(false);
                  }}
                >
                  Use Mock instead
                </Button>
              </div>
            </Banner>
          )}
          {runError && (
            <Banner title="Something went wrong">
              <p className="text-xs text-muted-foreground">{runError}</p>
            </Banner>
          )}
        </div>
      )}

      {/* Automation: video first, controls under it. Custom: preview + tools. */}
      <div className="flex min-h-0 flex-1 flex-col">
        {mode === "automation" ? (
          <>
            <div className="flex min-h-0 flex-1 flex-col">
              <CenterCanvas
                ctx={ctx}
                mode={previewMode}
                onModeChange={setPreviewMode}
                originalRef={originalRef}
                resultRef={resultRef}
                currentTime={currentTime}
                onSeek={seek}
                onTimeChange={handleTimeChange}
                diagnose={diagnose}
                onCaptionPosition={handleCaptionPosition}
              />
            </div>
            <LeftPanel
              ctx={ctx}
              onPickVideo={() => void pickVideo()}
              onPreviewResult={handlePreviewResult}
            />
            <Timeline ctx={ctx} currentTime={currentTime} onSeek={seek} />
          </>
        ) : (
          <div className="flex min-h-0 w-full flex-1 flex-col">
            <CustomHeader
              project={project}
              meta={meta}
              artifacts={artifacts}
              resultReady={resultReady}
              onOpenVideo={() => void pickVideo()}
              onOpenOutputFolder={() => void handleOpenOutputFolder()}
              onPreviewResult={handlePreviewResult}
              onResetTools={() => setCustomTools([])}
              onClearProject={() => void handleClearProject()}
            />
            <div className="flex min-h-0 flex-1">
              <CenterCanvas
                ctx={ctx}
                mode={previewMode}
                onModeChange={setPreviewMode}
                originalRef={originalRef}
                resultRef={resultRef}
                currentTime={currentTime}
                onSeek={seek}
                onTimeChange={handleTimeChange}
                diagnose={diagnose}
                onCaptionPosition={handleCaptionPosition}
              />
              <CustomToolPanel
                ctx={ctx}
                tools={customTools}
                onApply={applyCustomTool}
                onRemove={(id) => setCustomTools((current) => current.filter((t) => t.id !== id))}
                onReset={() => setCustomTools([])}
              />
            </div>
          </div>
        )}
      </div>

      {mode === "custom" && phase === "succeeded" && artifacts && project && (
        <ResultCard
          inputPath={project.source_video_path}
          artifacts={artifacts}
          meta={meta}
          processingMs={
            finishedAtRef.current !== null && plan.startedAt
              ? finishedAtRef.current - plan.startedAt
              : null
          }
          onPreview={() => setPreviewMode("result")}
          onOpenFolder={() => void handleOpenOutputFolder()}
          onCopyPath={() => void handleCopyPath()}
        />
      )}

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
    </div>
  );
}

function Banner({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      data-role="banner"
      className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-3"
    >
      <p className="flex items-center gap-1.5 text-sm font-semibold text-destructive">
        <X className="size-4" aria-hidden="true" /> {title}
      </p>
      {children}
    </div>
  );
}
