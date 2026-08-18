import { useCallback, useRef, useState } from "react";
import { AudioLines, Captions, Clapperboard } from "lucide-react";

import { cn } from "@/components/ui/utils";
import { formatDuration } from "@/lib/format";
import type { WorkspaceContext } from "./types";

const TRACK_HEIGHT = 22;
const RULER_HEIGHT = 20;

/** Pick a "nice" tick step so the ruler shows ~10–24 labels. */
function tickStep(duration: number): number {
  const raw = duration / 14;
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
  for (const s of steps) {
    if (s >= raw) return s;
  }
  return 7200;
}

/**
 * BOTTOM — compact timeline with VIDEO / AUDIO / SUBTITLE tracks. The playhead
 * follows the real player time; clicking the ruler scrubs both players and
 * dragging a cue block persists the new timestamp through the cue backend.
 */
export default function Timeline({
  ctx,
  currentTime,
  onSeek,
}: {
  ctx: WorkspaceContext;
  currentTime: number;
  onSeek: (t: number) => void;
}) {
  const duration = ctx.meta?.duration ?? 0;
  const [dragId, setDragId] = useState<string | null>(null);
  const dragRef = useRef<{ id: string; startX: number; origStart: number; span: number } | null>(
    null,
  );
  const barRef = useRef<HTMLDivElement | null>(null);

  const toTime = useCallback(
    (clientX: number) => {
      const el = barRef.current;
      if (!el || duration <= 0) return 0;
      const rect = el.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return ratio * duration;
    },
    [duration],
  );

  const timeToPct = useCallback(
    (t: number) => (duration > 0 ? (Math.min(t, duration) / duration) * 100 : 0),
    [duration],
  );

  function onBarClick(e: React.MouseEvent) {
    if (dragId) return;
    onSeek(toTime(e.clientX));
  }

  function beginDrag(e: React.PointerEvent, cueId: string, start: number, end: number) {
    e.stopPropagation();
    e.preventDefault();
    setDragId(cueId);
    dragRef.current = { id: cueId, startX: e.clientX, origStart: start, span: end - start };
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointerup", onUp);
      const drag = dragRef.current;
      dragRef.current = null;
      setDragId(null);
      if (!drag) return;
      const delta = toTime(ev.clientX) - toTime(drag.startX);
      const next = Math.max(0, Math.min(duration - drag.span, drag.origStart + delta));
      if (Math.abs(next - drag.origStart) > 0.05) {
        void ctx.cues.update(drag.id, { start: next, end: next + drag.span });
        onSeek(next);
      }
    };
    window.addEventListener("pointerup", onUp);
  }

  if (duration <= 0) {
    return <div data-role="timeline" className="hidden" aria-hidden="true" />;
  }

  const step = tickStep(duration);
  const ticks: number[] = [];
  for (let t = 0; t <= duration; t += step) ticks.push(t);
  if (ticks[ticks.length - 1] !== duration) ticks.push(duration);

  const playheadPct = timeToPct(currentTime);

  return (
    <section
      data-role="timeline"
      className="shrink-0 border-t border-border bg-panel"
      aria-label="Timeline"
    >
      <div className="flex items-center justify-between px-3 pt-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          Timeline
        </p>
        <p className="text-[11px] tabular-nums text-muted-foreground">
          {formatDuration(duration)} · {ctx.cues.cues.length} cues
        </p>
      </div>

      <div
        ref={barRef}
        data-role="timeline-bar"
        className="relative mx-3 mb-2 select-none"
        onMouseDown={onBarClick}
      >
        {/* Ruler */}
        <div className="relative" style={{ height: RULER_HEIGHT }}>
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute top-0 -translate-x-1/2 text-[9px] tabular-nums text-muted-foreground/70"
              style={{ left: `${timeToPct(t)}%` }}
            >
              {formatDuration(t)}
            </span>
          ))}
        </div>

        {/* Tracks */}
        <TrackRow label="VIDEO" icon={Clapperboard} height={TRACK_HEIGHT} selected={false}>
          <div className="h-full w-full rounded-sm bg-gradient-to-r from-gold/50 to-gold/30" />
        </TrackRow>
        <TrackRow label="AUDIO" icon={AudioLines} height={TRACK_HEIGHT} selected={false}>
          <div className="h-full w-full rounded-sm bg-gradient-to-r from-sky-400/50 to-sky-400/25" />
        </TrackRow>
        <TrackRow label="SUBTITLE" icon={Captions} height={TRACK_HEIGHT} selected={false}>
          {ctx.cues.cues.map((cue) => {
            const left = timeToPct(cue.start);
            const width = Math.max(0.5, timeToPct(cue.end) - left);
            const selected = ctx.cues.selectedId === cue.id;
            return (
              <div
                key={cue.id}
                data-role="cue-block"
                data-selected={selected || undefined}
                onPointerDown={(e) => beginDrag(e, cue.id, cue.start, cue.end)}
                onClick={(e) => {
                  e.stopPropagation();
                  ctx.cues.select(cue.id);
                  onSeek(cue.start);
                }}
                title={`${formatDuration(cue.start)} → ${formatDuration(cue.end)}`}
                className={cn(
                  "absolute top-[2px] h-[calc(100%-4px)] cursor-ew-resize overflow-hidden rounded-sm border px-1 text-[8px] leading-[18px] text-foreground/90",
                  selected
                    ? "border-gold bg-gold/40"
                    : "border-border bg-gold/20 hover:border-gold/60",
                  dragId === cue.id && "z-10 opacity-90",
                )}
                style={{ left: `${left}%`, width: `${width}%` }}
              >
                {cue.text}
              </div>
            );
          })}
        </TrackRow>

        {/* Playhead */}
        <div
          data-role="timeline-playhead"
          className="pointer-events-none absolute top-0 z-20 h-full w-px bg-gold"
          style={{ left: `${playheadPct}%` }}
        >
          <span className="absolute -left-[3px] top-0 size-[7px] rounded-full bg-gold" />
        </div>
      </div>
    </section>
  );
}

function TrackRow({
  label,
  icon: Icon,
  height,
  selected,
  children,
}: {
  label: string;
  icon: typeof Clapperboard;
  height: number;
  selected: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 border-b border-border/60 last:border-b-0",
        selected && "bg-accent/40",
      )}
      style={{ height }}
    >
      <span className="flex w-20 shrink-0 items-center gap-1 pl-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        <Icon className="size-3" aria-hidden="true" />
        {label}
      </span>
      <div className="relative h-full min-w-0 flex-1">{children}</div>
    </div>
  );
}
