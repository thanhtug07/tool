import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import { ping } from "./bridge";

const mockedInvoke = vi.mocked(invoke);

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
    mockedInvoke.mockRejectedValue(new Error("window.__TAURI_INTERNALS__ is undefined"));

    await expect(ping()).rejects.toMatchObject({
      code: "E_IPC_UNAVAILABLE",
      message: "Cannot reach the Rust core. Run the app inside Tauri (npm run tauri dev).",
    });
  });
});
