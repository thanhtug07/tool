import * as React from "react";

export interface SpinnerProps extends React.HTMLAttributes<HTMLDivElement> {
  size?: "sm" | "md" | "lg";
  label?: string;
}

const sizes = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-10 w-10 border-[3px]",
};

function Spinner({ size = "md", label, className = "", ...props }: SpinnerProps) {
  return (
    <div role="status" className={"inline-flex items-center gap-2 " + className} {...props}>
      <div
        className={
          sizes[size] +
          " rounded-full border-current border-t-transparent animate-spin text-[var(--accent)]"
        }
      />
      {label && <span className="text-sm text-[var(--muted-foreground)]">{label}</span>}
    </div>
  );
}

export { Spinner };
