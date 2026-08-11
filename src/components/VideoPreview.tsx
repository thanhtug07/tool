import { useEffect, useRef, useState } from "react";

import type { SubtitleCue } from "@/api/subtitle";
import {
  ASS_DEFAULT_STYLE,
  activeCue,
  captionStyle,
  type SubtitleOverlayStyle,
} from "./subtitleOverlay";

export type VideoPreviewProps = {
  videoUrl: string;
  cues: SubtitleCue[];
  style?: SubtitleOverlayStyle;
  /** Rendered video content height the overlay scales against (default 1080). */
  videoHeightPx?: number;
  /** Seed the initial playhead (used by tests / resume). */
  initialTime?: number;
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
 * Video player with an HTML caption overlay (TASK-026). The overlay is a pure
 * function of the current playhead and the cue list; the player element only
 * reports time — no project/database logic lives here.
 */
export default function VideoPreview({
  videoUrl,
  cues,
  style = ASS_DEFAULT_STYLE,
  videoHeightPx = 1080,
  initialTime = 0,
}: VideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(initialTime);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // `timeupdate` alone fires ~4 Hz; a requestAnimationFrame loop keeps cue
  // switching inside the +/-50 ms tolerance noted in TASK-026.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let frame = 0;
    const tick = () => {
      setCurrentTime(video.currentTime);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const cue = activeCue(cues, currentTime);

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
    if (!video) return;
    video.currentTime = time;
    setCurrentTime(time);
  }

  return (
    <div
      data-role="video-preview"
      className="relative overflow-hidden rounded border border-border bg-black"
    >
      <video
        ref={videoRef}
        data-role="video-element"
        src={videoUrl}
        className="block w-full"
        aria-label="Video preview"
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onWaiting={() => setLoading(true)}
        onCanPlay={() => setLoading(false)}
        onError={() =>
          setError("Không thể mở video. Định dạng không được hỗ trợ hoặc file không tồn tại.")
        }
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
          className="absolute inset-0 grid place-items-center bg-black/70 p-4 text-center text-sm text-white"
        >
          {error}
        </div>
      )}
      {cue && !error && (
        <div
          data-role="caption"
          data-cue-number={cue.cue_number}
          style={captionStyle(style, videoHeightPx)}
        >
          {cue.text}
        </div>
      )}

      <div className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-black/60 p-2">
        <button
          type="button"
          data-role="play-toggle"
          onClick={togglePlay}
          className="rounded bg-white/10 px-3 py-1 text-xs text-white"
        >
          {playing ? "Pause" : "Play"}
        </button>
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
      </div>
    </div>
  );
}
