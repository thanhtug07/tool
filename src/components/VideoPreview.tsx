import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

import type { SubtitleCue } from "@/api/subtitle";
import {
  ASS_DEFAULT_STYLE,
  activeCue,
  captionStyle,
  type SubtitleOverlayStyle,
} from "./subtitleOverlay";
import { cn } from "./ui/utils";

export type VideoPreviewProps = {
  videoUrl: string;
  cues: SubtitleCue[];
  style?: SubtitleOverlayStyle;
  /** Extra classes for the outer frame (e.g. `h-full` to fill the stage). */
  className?: string;
  /** Rendered video content height the overlay scales against (default 1080). */
  videoHeightPx?: number;
  /** Seed the initial playhead (used by tests / resume). */
  initialTime?: number;
  /** Real media metadata read from the loaded element (duration/resolution). */
  onMetadata?: (meta: { duration: number; width: number; height: number }) => void;
  /**
   * Playhead drift callback (fires from a rAF loop, ~60 Hz). The workspace uses
   * it to keep the timeline/right panel in sync with the player.
   */
  onTimeChange?: (time: number) => void;
  /** Play/pause state changes (user interaction or programmatic). */
  onPlayStateChange?: (playing: boolean) => void;
  /**
   * Real playback-speed multiplier (1 = normal). Changes apply to the element.
   */
  playbackRate?: number;
  /**
   * Fit mode for the frame. `contain` letterboxes the full frame; `fill`
   * stretches to the canvas box.
   */
  fit?: "contain" | "fill";
  /** 0..1 muted state (mirrors the element so the transport stays in sync). */
  muted?: boolean;
  /** 0..1 volume (mirrors the element so the transport stays in sync). */
  volume?: number;
  /**
   * Called once when the `video` element errors. Returns an optional detailed
   * message (e.g. from a real ffprobe run) shown under the generic one so
   * "format not supported" failures are actionable instead of dead ends.
   */
  diagnose?: () => Promise<string | null>;
  /**
   * Drag callback for `style.position === "custom"`: fires with the new
   * caption anchor as fractions (0..1) of the visible video content, clamped
   * to the draggable area. The workspace persists it and the render stage
   * burns the caption at that spot.
   */
  onPositionChange?: (x: number, y: number) => void;
  /**
   * Hide the built-in bottom control bar — the studio canvas draws its own
   * transport row and drives the player through the ref handle.
   */
  showControls?: boolean;
};

/** Imperative player surface used by the timeline, transcript, preview tabs. */
export type VideoPreviewHandle = {
  togglePlay(): void;
  play(): void;
  pause(): void;
  seekTo(seconds: number): void;
  setVolume(value: number): void;
  toggleMuted(): void;
  setPlaybackRate(rate: number): void;
  toggleFullscreen(): void;
  getCurrentTime(): number;
  getDuration(): number;
};

export function formatTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const minutes = Math.floor(s / 60);
  const totalMs = Math.round((s % 60) * 1000);
  const sec = Math.floor(totalMs / 1000);
  const frac = String(totalMs % 1000).padStart(3, "0");
  return `${minutes}:${String(sec).padStart(2, "0")}.${frac}`;
}

/**
 * Visible content rect of a `object-fit: contain` video element: the video
 * fills its box letterboxed, so the caption anchor must be measured against
 * the *content* rect, not the element box, to match the burned-in frame.
 */
export function videoContentRect(
  video: HTMLVideoElement,
): { left: number; top: number; width: number; height: number } | null {
  const box = video.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0 || !video.videoWidth || !video.videoHeight) return null;
  const scale = Math.min(box.width / video.videoWidth, box.height / video.videoHeight);
  const width = video.videoWidth * scale;
  const height = video.videoHeight * scale;
  return {
    left: box.left + (box.width - width) / 2,
    top: box.top + (box.height - height) / 2,
    width,
    height,
  };
}

/**
 * Video player with an HTML caption overlay (TASK-026). The overlay is a pure
 * function of the current playhead and the cue list; the player element only
 * reports time — no project/database logic lives here. The transport is
 * fully custom (no native controls) and exposed programmatically through the
 * ref handle so the studio timeline can drive it.
 */
const VideoPreview = forwardRef<VideoPreviewHandle, VideoPreviewProps>(function VideoPreview(
  {
    videoUrl,
    cues,
    style = ASS_DEFAULT_STYLE,
    videoHeightPx = 1080,
    initialTime = 0,
    onMetadata,
    onTimeChange,
    onPlayStateChange,
    playbackRate = 1,
    fit = "contain",
    muted: mutedProp,
    volume: volumeProp,
    diagnose,
    onPositionChange,
    showControls = true,
    className,
  }: VideoPreviewProps,
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(initialTime);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  // `timeupdate` alone fires ~4 Hz; a requestAnimationFrame loop keeps cue
  // switching inside the +/-50 ms tolerance noted in TASK-026 and lets the
  // workspace timeline track the playhead smoothly.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let frame = 0;
    const tick = () => {
      const t = video.currentTime;
      setCurrentTime(t);
      onTimeChange?.(t);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [onTimeChange]);

  // A new *source* must reset transient player state (the previous source's
  // error overlay would otherwise persist and mask the fresh video). A cue
  // refresh must NOT touch the playhead/duration — editing a subtitle would
  // otherwise yank the video back to 0 on every save (UX review finding).
  const lastSourceRef = useRef(videoUrl);
  useEffect(() => {
    if (videoUrl === lastSourceRef.current) return;
    lastSourceRef.current = videoUrl;
    setError(null);
    setDiagnosis(null);
    setLoading(true);
    setDuration(0);
    setPlaying(false);
    onPlayStateChange?.(false);
    setCurrentTime(0);
  }, [videoUrl, onPlayStateChange]);

  // Apply the external playback-rate / mute / volume mirrors whenever they
  // change (the workspace controls live in the Timeline/transport row).
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = playbackRate;
  }, [playbackRate, videoUrl]);
  useEffect(() => {
    const video = videoRef.current;
    if (video && mutedProp !== undefined) video.muted = mutedProp;
    if (video && mutedProp !== undefined) setMuted(mutedProp);
  }, [mutedProp, videoUrl]);
  useEffect(() => {
    const video = videoRef.current;
    if (video && volumeProp !== undefined) {
      video.volume = Math.min(1, Math.max(0, volumeProp));
      setVolume(Math.min(1, Math.max(0, volumeProp)));
    }
  }, [volumeProp, videoUrl]);

  const cue = activeCue(cues, currentTime);

  // Dragging the caption (custom position) maps the pointer to fractions of
  // the visible video content, clamped to the draggable area, and reports
  // them through `onPositionChange` for the workspace to persist.
  const draggingRef = useRef(false);
  function captionPointerDown(event: React.PointerEvent) {
    // Any grab starts a reposition drag — the first move switches the overlay
    // to `custom` (a plain click without movement changes nothing).
    event.preventDefault();
    draggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function captionPointerMove(event: React.PointerEvent) {
    if (!draggingRef.current || !onPositionChange) return;
    const video = videoRef.current;
    const rect = video ? videoContentRect(video) : null;
    if (!rect) return;
    const x = Math.min(0.92, Math.max(0.08, (event.clientX - rect.left) / rect.width));
    const y = Math.min(0.94, Math.max(0.06, (event.clientY - rect.top) / rect.height));
    onPositionChange(x, y);
  }
  function captionPointerUp() {
    draggingRef.current = false;
  }

  function emitPlayState(next: boolean) {
    setPlaying(next);
    onPlayStateChange?.(next);
  }

  function togglePlay() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
    }
  }

  function seekTo(time: number) {
    const video = videoRef.current;
    if (!video || !Number.isFinite(time)) return;
    video.currentTime = time;
    setCurrentTime(time);
    onTimeChange?.(time);
  }

  function toggleMuted() {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !video.muted;
    setMuted(video.muted);
  }

  function changeVolume(next: number) {
    const video = videoRef.current;
    if (!video) return;
    const clamped = Math.min(1, Math.max(0, next));
    video.volume = clamped;
    video.muted = clamped === 0;
    setVolume(clamped);
    setMuted(clamped === 0);
  }

  async function toggleFullscreen() {
    const container = containerRef.current;
    if (!container) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await container.requestFullscreen();
      }
    } catch {
      // Fullscreen can be blocked by the webview; the button stays harmless.
    }
  }

  useEffect(() => {
    const handler = () => setFullscreen(document.fullscreenElement !== null);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  function handleError() {
    setError("Không thể mở video. Định dạng không được hỗ trợ hoặc file không tồn tại.");
    if (diagnose) {
      void diagnose()
        .then((message) => {
          if (message) setDiagnosis(message);
        })
        .catch(() => {
          setDiagnosis(null);
        });
    }
  }

  useImperativeHandle(
    ref,
    () => ({
      togglePlay,
      play: () => void videoRef.current?.play(),
      pause: () => videoRef.current?.pause(),
      seekTo,
      setVolume: changeVolume,
      toggleMuted,
      setPlaybackRate: (rate: number) => {
        const video = videoRef.current;
        if (video) video.playbackRate = rate;
      },
      toggleFullscreen: () => void toggleFullscreen(),
      getCurrentTime: () => videoRef.current?.currentTime ?? 0,
      getDuration: () => videoRef.current?.duration ?? duration,
    }),
    // `duration` is intentionally read from state for the handle; the player
    // element is the authoritative time source.
    [duration],
  );

  return (
    <div
      ref={containerRef}
      data-role="video-preview"
      className={cn("relative overflow-hidden rounded border border-border bg-black", className)}
    >
      <video
        ref={videoRef}
        data-role="video-element"
        src={videoUrl}
        className={fit === "fill" ? "block h-full w-full" : "block h-full w-full object-contain"}
        aria-label="Video preview"
        onLoadedMetadata={(event) => {
          const el = event.currentTarget;
          setDuration(el.duration || 0);
          onMetadata?.({
            duration: el.duration || 0,
            width: el.videoWidth,
            height: el.videoHeight,
          });
        }}
        onPlay={() => emitPlayState(true)}
        onPause={() => emitPlayState(false)}
        onWaiting={() => setLoading(true)}
        onCanPlay={() => setLoading(false)}
        onError={handleError}
      />

      {loading && !error && (
        <div
          data-role="video-loading"
          className="absolute inset-0 grid place-items-center text-sm text-white/70"
        >
          Loading…
        </div>
      )}
      {error && (
        <div
          data-role="video-error"
          className="absolute inset-0 grid place-items-center bg-black/75 p-6 text-center text-sm text-white"
        >
          <div className="max-w-md space-y-2">
            <p>{error}</p>
            {diagnosis && <p className="text-xs text-white/75">{diagnosis}</p>}
          </div>
        </div>
      )}
      {cue && !error && (
        <div
          data-role="caption"
          data-cue-number={cue.cue_number}
          data-position={style.position}
          style={captionStyle(style, videoHeightPx)}
          onPointerDown={captionPointerDown}
          onPointerMove={captionPointerMove}
          onPointerUp={captionPointerUp}
          onPointerCancel={captionPointerUp}
          title="Drag to reposition the subtitle on the video"
        >
          {cue.text}
        </div>
      )}

      {showControls && (
        <div className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-black/60 p-2">
          <button
            type="button"
            data-role="play-toggle"
            onClick={togglePlay}
            className="rounded bg-white/10 px-3 py-1 text-xs text-white hover:bg-white/20"
          >
            {playing ? "Pause" : "Play"}
          </button>
          <button
            type="button"
            data-role="volume-toggle"
            onClick={toggleMuted}
            aria-label={muted ? "Unmute" : "Mute"}
            title={muted ? "Unmute" : "Mute"}
            className="rounded bg-white/10 px-2 py-1 text-xs text-white hover:bg-white/20"
          >
            {muted ? "Muted" : "Vol"}
          </button>
          <input
            type="range"
            data-role="volume"
            aria-label="Volume"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(event) => changeVolume(Number(event.target.value))}
            className="w-16"
          />
          <input
            type="range"
            data-role="scrub"
            aria-label="Scrub"
            min={0}
            max={duration || 0}
            step={0.05}
            value={Math.min(currentTime, duration || 0)}
            onChange={(event) => seekTo(Number(event.target.value))}
            className="flex-1"
          />
          <span className="shrink-0 text-xs tabular-nums text-white/80">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
          <button
            type="button"
            data-role="fullscreen"
            onClick={() => void toggleFullscreen()}
            aria-label="Fullscreen"
            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
            className="rounded bg-white/10 px-2 py-1 text-xs text-white hover:bg-white/20"
          >
            ⛶
          </button>
        </div>
      )}
    </div>
  );
});

export default VideoPreview;
