import { safeInvoke } from "@/api/invoke";

export type PingResult = {
  response: "pong";
  latencyMs: number;
};

export type PingError = {
  code: "E_IPC_UNAVAILABLE";
  message: string;
};

/**
 * Calls the `ping` command on the Rust core and measures round-trip latency.
 *
 * Rejects with a typed, human-readable `PingError` (no raw stack traces) when
 * the backend is unreachable — e.g. the app is opened outside the Tauri shell.
 */
export async function ping(): Promise<PingResult> {
  const started = performance.now();
  try {
    const response = await safeInvoke<string>("ping");
    if (response !== "pong") {
      throw {
        code: "E_IPC_UNAVAILABLE",
        message: "Unexpected response from the Rust core.",
      } satisfies PingError;
    }
    return { response, latencyMs: Math.round(performance.now() - started) };
  } catch (error) {
    throw isPingError(error)
      ? error
      : ({
          code: "E_IPC_UNAVAILABLE",
          message: "Cannot reach the Rust core. Run the app inside Tauri (npm run tauri dev).",
        } satisfies PingError);
  }
}

function isPingError(value: unknown): value is PingError {
  return typeof value === "object" && value !== null && "code" in value && "message" in value;
}
