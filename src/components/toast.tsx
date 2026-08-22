import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckCircle2, XCircle, Info, X } from "lucide-react";
import { cn } from "@/components/ui/utils";

export type ToastKind = "info" | "success" | "error";

export type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
};

type ToastContextValue = {
  push: (message: string, kind?: ToastKind) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (message: string, kind: ToastKind = "info") => {
      const id = nextId.current;
      nextId.current += 1;
      setToasts((current) => [...current, { id, kind, message }]);
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (value === null) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return value;
}

const TOAST_ICONS: Record<ToastKind, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: XCircle,
  info: Info,
};

const TOAST_STYLES: Record<ToastKind, { border: string; icon: string; bg: string }> = {
  success: {
    border: "border-success/30",
    icon: "text-success",
    bg: "bg-success/5",
  },
  error: {
    border: "border-destructive/30",
    icon: "text-destructive",
    bg: "bg-destructive/5",
  },
  info: {
    border: "border-info/30",
    icon: "text-info",
    bg: "bg-info/5",
  },
};

export function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      data-role="toast-viewport"
      aria-live="polite"
      className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2"
    >
      {toasts.map((toast) => {
        const Icon = TOAST_ICONS[toast.kind];
        const styles = TOAST_STYLES[toast.kind];
        return (
          <div
            key={toast.id}
            data-role="toast"
            data-kind={toast.kind}
            className={cn(
              "pointer-events-auto animate-slide-in-right rounded-lg border p-3 shadow-[var(--shadow-md)]",
              "bg-card",
              styles.border,
            )}
          >
            <div className="flex items-start gap-2.5">
              <span className={cn("mt-0.5 shrink-0", styles.icon)}>
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <p className="min-w-0 flex-1 text-sm text-foreground">{toast.message}</p>
              <button
                type="button"
                data-role="toast-dismiss"
                aria-label="Dismiss notification"
                className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                onClick={() => onDismiss(toast.id)}
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
            {/* Auto-dismiss progress bar */}
            <div className="mt-2 h-0.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full", styles.icon.replace("text-", "bg-"))}
                style={{ animation: "toast-progress " + AUTO_DISMISS_MS + "ms linear forwards" }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
