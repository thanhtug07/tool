import * as React from "react";

/* ---------- Context ---------- */
interface DialogContextValue {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DialogContext = React.createContext<DialogContextValue>({
  open: false,
  onOpenChange: () => {},
});

/* ---------- Root ---------- */
export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}

function Dialog({
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  children,
}: DialogProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;

  const handleChange = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  return (
    <DialogContext.Provider value={{ open, onOpenChange: handleChange }}>
      {children}
    </DialogContext.Provider>
  );
}

/* ---------- Trigger ---------- */
export interface DialogTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
}

function DialogTrigger({ children, asChild, ...props }: DialogTriggerProps) {
  const { onOpenChange } = React.useContext(DialogContext);
  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children as React.ReactElement<React.HTMLAttributes<HTMLElement>>, {
      onClick: (e: React.MouseEvent) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (children.props as any).onClick?.(e);
        onOpenChange(true);
      },
    });
  }
  return (
    <button type="button" onClick={() => onOpenChange(true)} {...props}>
      {children}
    </button>
  );
}

/* ---------- Portal ---------- */
function DialogPortal({ children }: { children: React.ReactNode }) {
  const { open } = React.useContext(DialogContext);
  if (!open) return null;
  return <>{children}</>;
}

/* ---------- Overlay ---------- */
function DialogOverlay({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const { onOpenChange } = React.useContext(DialogContext);
  return (
    <div
      className={
        "fixed inset-0 z-50 bg-black/60 backdrop-blur-sm animate-in fade-in-0 " + className
      }
      onClick={() => onOpenChange(false)}
      {...props}
    />
  );
}

/* ---------- Content ---------- */
export interface DialogContentProps extends React.HTMLAttributes<HTMLDivElement> {
  showClose?: boolean;
}

function DialogContent({
  children,
  className = "",
  showClose = true,
  ...props
}: DialogContentProps) {
  const { onOpenChange } = React.useContext(DialogContext);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onOpenChange]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className={
        "fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 " +
        "rounded-lg border border-[var(--border)] bg-[var(--card)] p-6 shadow-[var(--shadow-lg)] " +
        "animate-in fade-in-0 zoom-in-95 " +
        className
      }
      {...props}
    >
      {children}
      {showClose && (
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 15 15"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M11.7816 4.03157C12.0062 3.80702 12.0062 3.44295 11.7816 3.2184C11.5571 2.99385 11.193 2.99385 10.9685 3.2184L7.50005 6.68682L4.03164 3.2184C3.80708 2.99385 3.44301 2.99385 3.21846 3.2184C2.99391 3.44295 2.99391 3.80702 3.21846 4.03157L6.68688 7.49999L3.21846 10.9684C2.99391 11.193 2.99391 11.557 3.21846 11.7816C3.44301 12.0061 3.80708 12.0061 4.03164 11.7816L7.50005 8.31316L10.9685 11.7816C11.193 12.0061 11.5571 12.0061 11.7816 11.7816C12.0062 11.557 12.0062 11.193 11.7816 10.9684L8.31322 7.49999L11.7816 4.03157Z"
              fill="currentColor"
              fillRule="evenodd"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}
    </div>
  );
}

/* ---------- Header / Footer / Title / Description ---------- */
function DialogHeader({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={"flex flex-col space-y-1.5 text-center sm:text-left " + className} {...props} />
  );
}

function DialogFooter({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={"flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-4 " + className}
      {...props}
    />
  );
}

function DialogTitle({ className = "", ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={
        "text-lg font-semibold leading-none tracking-tight text-[var(--foreground)] " + className
      }
      {...props}
    />
  );
}

function DialogDescription({
  className = "",
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={"text-sm text-[var(--muted-foreground)] " + className} {...props} />;
}

/* ---------- Close ---------- */
function DialogClose({
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { onOpenChange } = React.useContext(DialogContext);
  return (
    <button type="button" onClick={() => onOpenChange(false)} className={className} {...props}>
      {children}
    </button>
  );
}

export {
  Dialog,
  DialogTrigger,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
  DialogClose,
};
