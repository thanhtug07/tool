import * as React from "react";
import { cn } from "@/components/ui/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Show an icon on the left side. */
  icon?: React.ReactNode;
  /** Show an error message below the input. */
  error?: string;
}

/**
 * Styled text input with optional icon and error state.
 * Replaces raw <input> elements across the app.
 */
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, icon, error, ...props }, ref) => {
    return (
      <div className="w-full">
        <div className="relative">
          {icon && (
            <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground">
              {icon}
            </span>
          )}
          <input
            type={type}
            className={cn(
              "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm",
              "transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium",
              "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-50",
              icon && "pl-8",
              error && "border-destructive focus-visible:ring-destructive",
              className,
            )}
            ref={ref}
            {...props}
          />
        </div>
        {error && (
          <p className="mt-1 text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  },
);
Input.displayName = "Input";

export { Input };
