import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

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

const AUTO_DISMISS_MS = 5000;

/** Provides a simple toast queue; `useToast()` pushes to it. */
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

/** Read the toast pusher (throws outside a provider). */
export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (value === null) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return value;
}

/** Presentational toast stack (exported for tests). */
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
      {toasts.map((toast) => (
        <div
          key={toast.id}
          data-role="toast"
          data-kind={toast.kind}
          className="pointer-events-auto rounded border border-border bg-background p-3 text-sm shadow-lg"
        >
          <div className="flex items-start justify-between gap-2">
            <span>{toast.message}</span>
            <button
              type="button"
              data-role="toast-dismiss"
              aria-label="Dismiss notification"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => onDismiss(toast.id)}
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
