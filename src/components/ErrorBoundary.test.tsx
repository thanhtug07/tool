import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import ErrorBoundary, { ErrorFallback } from "./ErrorBoundary";

describe("ErrorBoundary (unit — state logic + fallback)", () => {
  it("derives fallback state from a render error", () => {
    const state = ErrorBoundary.getDerivedStateFromError(new Error("boom"));
    expect(state).toEqual({ hasError: true, message: "boom" });
  });

  it("derives fallback state from a non-Error throw", () => {
    const state = ErrorBoundary.getDerivedStateFromError("boom-string");
    expect(state.hasError).toBe(true);
    expect(state.message).toBe("boom-string");
  });

  it("renders children when healthy", () => {
    const html = renderToStaticMarkup(
      <ErrorBoundary>
        <p>healthy content</p>
      </ErrorBoundary>,
    );
    expect(html).toContain("healthy content");
    expect(html).not.toContain('data-role="error-fallback"');
  });

  it("renders a user-friendly fallback with reload and no raw stack", () => {
    const html = renderToStaticMarkup(<ErrorFallback message="TypeError: x is undefined" />);
    expect(html).toContain("Something went wrong");
    expect(html).toContain('data-role="error-reload"');
    expect(html).toContain("Reload app");
    // The message is shown but the fallback never prints a stack trace body.
    expect(html).not.toContain("at ErrorBoundary");
    expect(html).not.toContain("componentStack");
  });
});
