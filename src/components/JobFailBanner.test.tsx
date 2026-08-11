import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { JobFailBannerList, reduceJobFailures, type JobFailure } from "./JobFailBanner";

const FAILED_EVENT = {
  jobId: "job_0001",
  status: "failed" as const,
  progress: 0.5,
  stage: "render",
  error: { code: "E_RENDER_VALIDATION", message: "render validation failed: duration drifted" },
};

describe("reduceJobFailures (pure)", () => {
  it("adds a failed job with code and message", () => {
    const failures = reduceJobFailures([], FAILED_EVENT);
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({
      jobId: "job_0001",
      code: "E_RENDER_VALIDATION",
      stage: "render",
      expanded: false,
    });
  });

  it("ignores non-failed events", () => {
    const failures = reduceJobFailures(
      [{ jobId: "job_0001", stage: "render", code: null, message: "x", expanded: false }],
      { ...FAILED_EVENT, status: "running" },
    );
    expect(failures).toHaveLength(1);
    expect(failures[0].code).toBeNull();
  });

  it("deduplicates by job id (newest first)", () => {
    const first = reduceJobFailures([], FAILED_EVENT);
    const second = reduceJobFailures(first, {
      ...FAILED_EVENT,
      error: { code: "E_RENDER_FAILED", message: "no" },
    });
    expect(second).toHaveLength(1);
    expect(second[0].code).toBe("E_RENDER_FAILED");
  });

  it("keeps at most 5 failures", () => {
    let failures: JobFailure[] = [];
    for (let i = 1; i <= 7; i += 1) {
      failures = reduceJobFailures(failures, {
        ...FAILED_EVENT,
        jobId: `job_${i}`,
        error: { code: "E_X", message: `m${i}` },
      });
    }
    expect(failures).toHaveLength(5);
    expect(failures[0].jobId).toBe("job_7");
  });

  it("falls back to a generic message when no error payload arrives", () => {
    const failures = reduceJobFailures([], { ...FAILED_EVENT, error: null });
    expect(failures[0].message).toContain("failed without a message");
  });
});

describe("JobFailBannerList (unit — static render)", () => {
  const FAILURES: JobFailure[] = [
    {
      jobId: "job_0001",
      stage: "render",
      code: "E_RENDER_VALIDATION",
      message: "render validation failed",
      expanded: true,
    },
  ];

  it("renders nothing with no failures", () => {
    const html = renderToStaticMarkup(
      <JobFailBannerList
        failures={[]}
        onDismiss={() => {}}
        onRetry={() => {}}
        onToggle={() => {}}
      />,
    );
    expect(html).toBe("");
  });

  it("shows code, retry, dismiss, and expanded detail", () => {
    const html = renderToStaticMarkup(
      <JobFailBannerList
        failures={FAILURES}
        onDismiss={() => {}}
        onRetry={() => {}}
        onToggle={() => {}}
      />,
    );
    expect(html).toContain("Job failed");
    expect(html).toContain("E_RENDER_VALIDATION");
    expect(html).toContain('data-role="job-fail-retry"');
    expect(html).toContain('data-role="job-fail-dismiss"');
    expect(html).toContain('data-role="job-fail-detail"');
  });

  it("collapses detail when not expanded", () => {
    const html = renderToStaticMarkup(
      <JobFailBannerList
        failures={[{ ...FAILURES[0], expanded: false }]}
        onDismiss={() => {}}
        onRetry={() => {}}
        onToggle={() => {}}
      />,
    );
    expect(html).not.toContain('data-role="job-fail-detail"');
  });
});
