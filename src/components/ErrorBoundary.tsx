import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  /** Optional sink for diagnostics (e.g. toasts); never shows stack traces raw. */
  onError?: (error: Error, info: ErrorInfo) => void;
};

type State = {
  hasError: boolean;
  message: string;
};

/**
 * App-level error boundary (TASK-030): a render error in any subtree shows a
 * user-friendly fallback instead of a blank/crashed window. The message is
 * sanitized for display; raw stack traces go only to the `onError` sink.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: unknown): State {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }

  render() {
    if (this.state.hasError) {
      return <ErrorFallback message={this.state.message} />;
    }
    return this.props.children;
  }
}

/** Fallback shown when the subtree crashed. */
export function ErrorFallback({
  message,
  onReload = () => window.location.reload(),
}: {
  message: string;
  onReload?: () => void;
}) {
  return (
    <div
      data-role="error-fallback"
      className="flex h-full min-h-64 flex-col items-center justify-center gap-3 p-6 text-center"
    >
      <h1 className="text-lg font-semibold">Something went wrong</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        The app hit an unexpected error. Your projects and settings are safe — reload the window to
        continue.
      </p>
      {message && (
        <pre data-role="error-message" className="max-w-lg truncate text-xs text-destructive">
          {message}
        </pre>
      )}
      <button
        type="button"
        data-role="error-reload"
        className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground"
        onClick={onReload}
      >
        Reload app
      </button>
    </div>
  );
}
