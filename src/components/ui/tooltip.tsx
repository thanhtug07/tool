import * as React from "react";

export interface TooltipProps {
  children: React.ReactNode;
  content: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  delayMs?: number;
}

function Tooltip({ children, content, side = "top", delayMs = 400 }: TooltipProps) {
  const [open, setOpen] = React.useState(false);
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout>>(undefined);

  const show = React.useCallback(() => {
    clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setOpen(true), delayMs);
  }, [delayMs]);

  const hide = React.useCallback(() => {
    clearTimeout(timeoutRef.current);
    setOpen(false);
  }, []);

  React.useEffect(() => () => clearTimeout(timeoutRef.current), []);

  const pos = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-2",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-2",
    left: "right-full top-1/2 -translate-y-1/2 mr-2",
    right: "left-full top-1/2 -translate-y-1/2 ml-2",
  }[side];

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          className={
            "absolute z-50 whitespace-nowrap rounded-md bg-[var(--popover)] px-3 py-1.5 text-xs text-[var(--popover-foreground)] " +
            "shadow-[var(--shadow-md)] animate-in fade-in-0 zoom-in-95 " +
            pos
          }
        >
          {content}
        </span>
      )}
    </span>
  );
}

export { Tooltip };
