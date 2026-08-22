import * as React from "react";

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "text" | "circular" | "rectangular" | "card";
  width?: string | number;
  height?: string | number;
  lines?: number;
}

function Skeleton({
  variant = "text",
  width,
  height,
  lines,
  className = "",
  style,
  ...props
}: SkeletonProps) {
  if (variant === "text" && lines && lines > 1) {
    return (
      <div className={"flex flex-col gap-2 " + className} style={style} {...props}>
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className={
              "h-4 rounded bg-[var(--muted)] animate-shimmer " +
              (i === lines - 1 ? "w-3/4" : "w-full")
            }
            style={i === lines - 1 ? { width: "75%" } : undefined}
          />
        ))}
      </div>
    );
  }

  const base = "animate-shimmer bg-[var(--muted)]";
  const variants: Record<string, string> = {
    text: "h-4 rounded",
    circular: "rounded-full",
    rectangular: "rounded-md",
    card: "rounded-lg",
  };

  return (
    <div
      className={base + " " + variants[variant] + " " + className}
      style={{ width, height, ...style }}
      {...props}
    />
  );
}

export { Skeleton };
