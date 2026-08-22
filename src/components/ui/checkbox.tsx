import * as React from "react";
import { Check, Minus } from "lucide-react";
export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  indeterminate?: boolean;
}

const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  ({ className, indeterminate, checked, ...props }, ref) => {
    const resolvedRef = React.useCallback(
      (node: HTMLInputElement | null) => {
        if (node) node.indeterminate = !!indeterminate;
        if (typeof ref === "function") ref(node);
        else if (ref) ref.current = node;
      },
      [indeterminate, ref],
    );
    return (
      <label className="inline-flex cursor-pointer items-center gap-2">
        <span className="relative flex size-4 shrink-0 items-center justify-center">
          <input
            type="checkbox"
            ref={resolvedRef}
            checked={checked}
            className="peer size-4 rounded border border-input bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            {...props}
          />
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-primary-foreground opacity-0 transition-opacity peer-checked:opacity-100">
            {indeterminate ? <Minus className="size-3" /> : <Check className="size-3" />}
          </span>
        </span>
        {props.children && <span className="text-sm text-foreground">{props.children}</span>}
      </label>
    );
  },
);
Checkbox.displayName = "Checkbox";
export { Checkbox };
