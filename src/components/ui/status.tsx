import type { ReactNode } from "react";

import { cn } from "./utils";

/** Tone → dot color. */
export type StatusTone = "ok" | "warn" | "bad" | "muted" | "info";

export const DOT_TONES: Record<StatusTone, string> = {
  ok: "bg-emerald-400",
  warn: "bg-amber-400",
  bad: "bg-red-500",
  muted: "bg-muted-foreground/60",
  info: "bg-sky-400",
};

/** Small colored status dot (worker dot, stage dot, …). */
export function StatusDot({ tone, className }: { tone: StatusTone; className?: string }) {
  return (
    <span
      className={cn("size-2.5 shrink-0 rounded-full", DOT_TONES[tone], className)}
      aria-hidden="true"
    />
  );
}

const BADGE_TONES: Record<StatusTone, string> = {
  ok: "bg-emerald-400/10 text-emerald-400 ring-1 ring-emerald-500/20",
  warn: "bg-amber-400/10 text-amber-400 ring-1 ring-amber-500/20",
  bad: "bg-red-500/10 text-red-400 ring-1 ring-red-500/20",
  muted: "bg-muted/60 text-muted-foreground ring-1 ring-border/40",
  info: "bg-sky-400/10 text-sky-400 ring-1 ring-sky-500/20",
};

/** Compact status pill. */
export function StatusBadge({
  tone,
  children,
  className,
}: {
  tone: StatusTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        BADGE_TONES[tone],
        className,
      )}
    >
      <StatusDot tone={tone} className="size-1.5" />
      {children}
    </span>
  );
}

/** Determinate progress bar (real values only — never faked). */
export function ProgressBar({
  value,
  className,
  label,
}: {
  /** 0..1 progress. */
  value: number;
  className?: string;
  label?: string;
}) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}
