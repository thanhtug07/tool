import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/invoke", () => ({
  safeInvoke: vi.fn(),
}));

import { safeInvoke } from "@/api/invoke";
import { ping } from "./bridge";

const mockedInvoke = vi.mocked(safeInvoke);

describe("bridge.ping (unit — mocked invoke)", () => {
  beforeEach(() => {
    mockedInvoke.mockReset();
    vi.useRealTimers();
  });

  it("resolves with pong and a latency when the backend answers", async () => {
    mockedInvoke.mockResolvedValue("pong");

    const result = await ping();

    expect(mockedInvoke).toHaveBeenCalledWith("ping");
    expect(result.response).toBe("pong");
    expect(result.latencyMs).toBeTypeOf("number");
    expect(result.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it("rejects with a typed, human-readable error when the backend is unreachable", async () => {
    mockedInvoke.mockRejectedValue(new Error("Unreachable core"));

    await expect(ping()).rejects.toMatchObject({
      code: "E_IPC_UNAVAILABLE",
    });
  });
});
