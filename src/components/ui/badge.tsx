import * as React from "react";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "secondary" | "destructive" | "outline" | "success" | "warning" | "info";
  size?: "sm" | "md";
}

const variantClasses: Record<string, string> = {
  default: "bg-[var(--accent)] text-[var(--accent-foreground)]",
  secondary: "bg-[var(--secondary)] text-[var(--secondary-foreground)]",
  destructive: "bg-[var(--destructive)] text-white",
  outline: "border border-[var(--border)] text-[var(--foreground)]",
  success: "bg-[var(--success)] text-white",
  warning: "bg-[var(--warning)] text-[var(--foreground)]",
  info: "bg-[var(--info)] text-white",
};

const sizeClasses: Record<string, string> = {
  sm: "px-1.5 py-0.5 text-[10px]",
  md: "px-2.5 py-0.5 text-xs",
};

function Badge({ variant = "default", size = "md", className = "", ...props }: BadgeProps) {
  return (
    <span
      className={
        "inline-flex items-center rounded-full font-medium transition-colors " +
        variantClasses[variant] +
        " " +
        sizeClasses[size] +
        " " +
        className
      }
      {...props}
    />
  );
}

export { Badge };
