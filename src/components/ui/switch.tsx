import * as React from "react";

export interface SwitchProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
  error?: string;
}

const Switch = React.forwardRef<HTMLInputElement, SwitchProps>(
  ({ label, error, className = "", disabled, ...props }, ref) => {
    const id = React.useId();

    return (
      <label
        htmlFor={id}
        className={
          "flex items-center gap-3 select-none " +
          (disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer") +
          " " +
          className
        }
      >
        <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
          <input
            ref={ref}
            type="checkbox"
            id={id}
            role="switch"
            disabled={disabled}
            className="peer sr-only"
            {...props}
          />
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-[var(--border-strong)] transition-colors
              peer-checked:bg-[var(--accent)]
              peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--accent)]"
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-[var(--background)] shadow-[var(--shadow-xs)] transition-transform
              peer-checked:translate-x-5"
          />
        </span>
        {label && <span className="text-sm text-[var(--foreground)]">{label}</span>}
        {error && <span className="text-xs text-[var(--destructive)]">{error}</span>}
      </label>
    );
  },
);
Switch.displayName = "Switch";

export { Switch };
