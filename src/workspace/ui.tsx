import type { ReactNode } from "react";

import { cn } from "@/components/ui/utils";
import { StatusDot } from "@/components/ui/status";

export type Tone = "ok" | "warn" | "bad" | "muted" | "info" | "on";

/** Compact section header used across the left/right panels. */
export function SectionHeader({
  title,
  hint,
  right,
  className,
}: {
  title: string;
  hint?: string;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          {title}
        </p>
        {hint && <p className="mt-0.5 text-[11px] text-muted-foreground/70">{hint}</p>}
      </div>
      {right}
    </div>
  );
}

/** Small tone-colored dot + text used for stage/status lines. */
export function ToneLine({
  tone,
  children,
  className,
}: {
  tone: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs", className)}>
      <ToneDot tone={tone} />
      {children}
    </span>
  );
}

function ToneDot({ tone }: { tone: Tone }) {
  if (tone === "on") {
    return <span className="size-2 animate-pulse rounded-full bg-gold" aria-hidden="true" />;
  }
  return <StatusDot tone={tone} className="size-2" aria-hidden="true" />;
}

/** Label/value row for metadata grids. */
export function InfoRow({
  label,
  value,
  title,
}: {
  label: string;
  value: ReactNode;
  title?: string;
}) {
  return (
    <div className="min-w-0" title={title}>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground/70">{label}</p>
      <p className="truncate text-xs text-foreground">{value}</p>
    </div>
  );
}

export function LabeledSelect(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
        {props.label}
      </span>
      <select
        id={props.id}
        className={cn(
          "h-7 w-full rounded border border-input bg-background px-2 text-xs text-foreground",
          props.className,
        )}
        value={props.value}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value)}
      >
        {props.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function CheckRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={cn("flex items-start gap-2 text-xs", disabled ? "opacity-50" : "cursor-pointer")}
    >
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="min-w-0">
        <span className="block text-foreground">{label}</span>
        {hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}
      </span>
    </label>
  );
}
