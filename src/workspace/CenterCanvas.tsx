import { useMemo, useState } from "react";
import { Loader2, Maximize2, Pause, Play, Upload, Volume2, VolumeX } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import VideoPreview, { formatTime, type VideoPreviewHandle } from "@/components/VideoPreview";
import { currentStageLabel } from "@/pages/Automation/automation";
import LogoRegionOverlay from "./LogoRegionOverlay";
import type { WorkspaceContext, PreviewMode } from "./types";

const PLAYBACK_SPEEDS = [0.5, 1, 1.25, 1.5, 2] as const;

/**
 * CENTER — the video stage. Original / Result / Split panes over a black
 * canvas, a real custom transport (play, time, volume, speed, fullscreen) and
 * a live automation overlay that reports the real job progress while running.
 */
export default function CenterCanvas({
  ctx,
  mode,
  onModeChange,
  originalRef,
  resultRef,
  currentTime,
  onSeek,
  onTimeChange,
  diagnose,
  onCaptionPosition,
}: {
  ctx: WorkspaceContext;
  mode: PreviewMode;
  onModeChange: (mode: PreviewMode) => void;
  originalRef: React.RefObject<VideoPreviewHandle | null>;
  resultRef: React.RefObject<VideoPreviewHandle | null>;
  currentTime: number;
  onSeek: (time: number) => void;
  onTimeChange: (time: number) => void;
  diagnose: () => Promise<string | null>;
  onCaptionPosition: (x: number, y: number) => void;
}) {
  const { project, videoUrl, resultUrl, phase, busy } = ctx;

  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [speed, setSpeed] = useState(1);

  const duration = useMemo(() => {
    const t = ctx.meta?.duration;
    return t && t > 0 ? t : 0;
  }, [ctx.meta]);

  const hasOriginal = Boolean(project && videoUrl);
  const hasResult = Boolean(resultUrl);

  const showOriginal = mode === "original" || mode === "split";
  const showResult = mode === "result" || mode === "split";

  function togglePlay() {
    if (mode !== "original") resultRef.current?.togglePlay();
    if (mode !== "result") originalRef.current?.togglePlay();
  }

  function toggleMuted() {
    setMuted((m) => !m);
    originalRef.current?.toggleMuted();
    resultRef.current?.toggleMuted();
  }

  function changeVolume(next: number) {
    setVolume(next);
    setMuted(next === 0);
    originalRef.current?.setVolume(next);
    resultRef.current?.setVolume(next);
  }

  function changeSpeed(next: number) {
    setSpeed(next);
    originalRef.current?.setPlaybackRate(next);
    resultRef.current?.setPlaybackRate(next);
  }

  function fullscreen() {
    const el = document.querySelector<HTMLElement>("[data-role='video-stage']");
    if (el && !document.fullscreenElement) void el.requestFullscreen();
    else void document.exitFullscreen();
  }

  if (!project) {
    return (
      <StageShell
        mode={mode}
        onModeChange={onModeChange}
        transport={
          <Transport
            disabled
            playing={false}
            currentTime={0}
            duration={0}
            muted={muted}
            volume={volume}
            speed={speed}
            onTogglePlay={togglePlay}
            onToggleMuted={toggleMuted}
            onVolume={changeVolume}
            onSpeed={changeSpeed}
            onFullscreen={fullscreen}
            onSeek={onSeek}
          />
        }
      >
        <div
          data-role="center-empty"
          className="grid h-full min-h-[320px] place-items-center border border-dashed border-border bg-card/40"
        >
          <div className="space-y-3 text-center">
            <Upload className="mx-auto size-9 text-muted-foreground" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium">Drop video here</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Drag &amp; drop your video — MP4, MKV, MOV, AVI, WebM, M4V
              </p>
            </div>
            <Button
              type="button"
              data-role="choose-video"
              disabled={busy}
              onClick={() => ctx.actions.pickVideo()}
            >
              Choose Video
            </Button>
          </div>
        </div>
      </StageShell>
    );
  }

  return (
    <StageShell
      mode={mode}
      onModeChange={onModeChange}
      transport={
        <Transport
          disabled={!hasOriginal && !hasResult}
          playing={false}
          currentTime={currentTime}
          duration={duration}
          muted={muted}
          volume={volume}
          speed={speed}
          onTogglePlay={togglePlay}
          onToggleMuted={toggleMuted}
          onVolume={changeVolume}
          onSpeed={changeSpeed}
          onFullscreen={fullscreen}
          onSeek={onSeek}
        />
      }
    >
      <div
        data-role="video-stage"
        className={cn(
          "relative grid h-full min-h-0 bg-black",
          mode === "split" ? "grid-cols-2" : "grid-cols-1",
        )}
      >
        {showOriginal &&
          (hasOriginal ? (
            <Pane label="Original" active={mode === "original"}>
              <div className="relative h-full min-h-0">
                <VideoPreview
                  ref={originalRef}
                  videoUrl={videoUrl}
                  cues={[]}
                  fit="contain"
                  muted={muted}
                  volume={volume}
                  playbackRate={speed}
                  showControls={false}
                  diagnose={diagnose}
                  onTimeChange={onTimeChange}
                  className="h-full rounded-none border-0"
                />
                {/* Amber selection rectangle while the Xóa logo panel is open. */}
                {ctx.logoRegion.region && <LogoRegionOverlay ctx={ctx} />}
              </div>
            </Pane>
          ) : (
            <EmptyPane text="Original video not available." />
          ))}
        {showResult &&
          (phase === "running" ? (
            <RunningOverlay ctx={ctx} />
          ) : hasResult ? (
            <Pane label="Automated Result" active={mode === "result"}>
              <VideoPreview
                ref={resultRef}
                videoUrl={resultUrl ?? ""}
                cues={ctx.cues.cues}
                style={ctx.overlay}
                fit="contain"
                muted={muted}
                volume={volume}
                playbackRate={speed}
                showControls={false}
                diagnose={diagnose}
                onTimeChange={onTimeChange}
                onPositionChange={onCaptionPosition}
                className="h-full rounded-none border-0"
              />
            </Pane>
          ) : phase === "succeeded" || phase === "cancelled" ? (
            <EmptyPane text="Rendered video not available." />
          ) : (
            <EmptyPane
              text="Waiting for automation."
              cta={phase === "idle" || phase === "failed" ? "Automate" : undefined}
              onCta={() => ctx.actions.automate()}
            />
          ))}
        {/* Compact floating Run / progress button — starts the real pipeline
            from either workspace (Automation or Custom). */}
        <FloatingRun ctx={ctx} />
      </div>
    </StageShell>
  );
}

// ---------------------------------------------------------------------------
// Shell pieces
// ---------------------------------------------------------------------------

function StageShell({
  mode,
  onModeChange,
  transport,
  children,
}: {
  mode: PreviewMode;
  onModeChange: (mode: PreviewMode) => void;
  transport: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Preview header + mode toggle */}
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-panel px-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          Preview
        </p>
        <div className="ml-auto flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5">
          {(
            [
              { key: "original", label: "Original" },
              { key: "result", label: "Result" },
              { key: "split", label: "Split" },
            ] as const
          ).map((m) => (
            <button
              key={m.key}
              type="button"
              data-role={`preview-mode-${m.key}`}
              onClick={() => onModeChange(m.key)}
              className={cn(
                "rounded px-2.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground",
                mode === m.key && "bg-accent text-accent-foreground",
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stage body */}
      <div className="flex min-h-0 flex-1 flex-col bg-black">{children}</div>

      {/* Transport */}
      {transport}
    </div>
  );
}

function Pane({
  label,
  active,
  children,
}: {
  label: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("relative min-h-0 min-w-0", active && "min-w-0")}>
      <span className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/80">
        {label}
      </span>
      {children}
    </div>
  );
}

function EmptyPane({ text, cta, onCta }: { text: string; cta?: string; onCta?: () => void }) {
  return (
    <div className="grid h-full min-h-[240px] place-items-center bg-black p-6 text-center">
      <div className="space-y-2">
        <p className="text-sm text-white/70">{text}</p>
        {cta && onCta && (
          <Button type="button" size="sm" onClick={onCta}>
            {cta}
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * Compact floating Run button pinned to the bottom-right of the video stage.
 * One control for both workspaces: idle → starts the real pipeline
 * (Automation or Custom — `ctx.actions.automate()` branches internally);
 * running → shows real progress and cancels on click; finished → replay.
 */
function FloatingRun({ ctx }: { ctx: WorkspaceContext }) {
  const { project, phase, busy } = ctx;
  if (!project) return null;
  const running = phase === "running";
  const finished = phase === "succeeded";
  const pct = Math.round(ctx.overallProgress * 100);
  return (
    <div className="pointer-events-none absolute bottom-3 right-3 z-20">
      <Button
        type="button"
        data-role="floating-run"
        size="sm"
        disabled={busy && !running}
        onClick={() => (running ? ctx.actions.cancel() : ctx.actions.automate())}
        title={running ? `Cancel — ${pct}%` : finished ? "Re-run pipeline" : "Run pipeline"}
        className={cn(
          "pointer-events-auto gap-1.5 shadow-lg shadow-black/50",
          running
            ? "border-red-400/40 bg-red-500/90 text-white hover:bg-red-500"
            : "bg-gold text-gold-foreground hover:bg-gold/90",
        )}
      >
        {running ? (
          <>
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {pct}%
          </>
        ) : (
          <>
            <Play className="size-3.5" aria-hidden="true" />
            {finished ? "Run again" : "Run"}
          </>
        )}
      </Button>
    </div>
  );
}

/** Compact processing overlay — progress from the actual job store. */
function RunningOverlay({ ctx }: { ctx: WorkspaceContext }) {
  const pct = Math.round(ctx.overallProgress * 100);
  return (
    <div
      data-role="processing-overlay"
      className="grid h-full min-h-[160px] place-items-center bg-black/90 p-4"
    >
      <div className="w-full max-w-xs space-y-2 text-center">
        <p className="text-sm text-white/90">
          {currentStageLabel(ctx.stages)} · {pct}%
        </p>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/15">
          <div
            className="h-full rounded-full bg-gold transition-all"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      </div>
    </div>
  );
}

/** Compact real transport — drives the visible players through their refs. */
function Transport({
  disabled,
  playing,
  currentTime,
  duration,
  muted,
  volume,
  speed,
  onTogglePlay,
  onToggleMuted,
  onVolume,
  onSpeed,
  onFullscreen,
  onSeek,
}: {
  disabled: boolean;
  playing: boolean;
  currentTime: number;
  duration: number;
  muted: boolean;
  volume: number;
  speed: number;
  onTogglePlay: () => void;
  onToggleMuted: () => void;
  onVolume: (v: number) => void;
  onSpeed: (v: number) => void;
  onFullscreen: () => void;
  onSeek: (time: number) => void;
}) {
  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-t border-border bg-panel px-3">
      <button
        type="button"
        data-role="transport-play"
        aria-label={playing ? "Pause" : "Play"}
        disabled={disabled}
        onClick={onTogglePlay}
        className="grid size-6 shrink-0 place-items-center rounded text-foreground hover:bg-accent disabled:opacity-40"
      >
        {playing ? (
          <Pause className="size-3.5" aria-hidden="true" />
        ) : (
          <Play className="size-3.5" aria-hidden="true" />
        )}
      </button>
      <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
        <span data-role="transport-time">{formatTime(currentTime)}</span> / {formatTime(duration)}
      </span>
      <input
        type="range"
        data-role="transport-scrub"
        aria-label="Scrub"
        min={0}
        max={duration || 0}
        step={0.05}
        value={Math.min(currentTime, duration || 0)}
        disabled={disabled}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="min-w-0 flex-1"
      />
      <button
        type="button"
        data-role="transport-mute"
        aria-label={muted ? "Unmute" : "Mute"}
        disabled={disabled}
        onClick={onToggleMuted}
        className="grid size-6 shrink-0 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
      >
        {muted ? (
          <VolumeX className="size-3.5" aria-hidden="true" />
        ) : (
          <Volume2 className="size-3.5" aria-hidden="true" />
        )}
      </button>
      <input
        type="range"
        data-role="transport-volume"
        aria-label="Volume"
        min={0}
        max={1}
        step={0.05}
        value={muted ? 0 : volume}
        disabled={disabled}
        onChange={(e) => onVolume(Number(e.target.value))}
        className="w-16 shrink-0"
      />
      <select
        data-role="transport-speed"
        aria-label="Playback speed"
        value={speed}
        disabled={disabled}
        onChange={(e) => onSpeed(Number(e.target.value))}
        className="h-6 shrink-0 rounded border border-input bg-background px-1 text-[11px]"
      >
        {PLAYBACK_SPEEDS.map((s) => (
          <option key={s} value={s}>
            {s}×
          </option>
        ))}
      </select>
      <button
        type="button"
        data-role="transport-fullscreen"
        aria-label="Fullscreen"
        disabled={disabled}
        onClick={onFullscreen}
        className="grid size-6 shrink-0 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
      >
        <Maximize2 className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}
