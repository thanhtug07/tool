/**
 * Web API command invocation wrapper.
 *
 * In pure web localhost mode, native desktop IPC is replaced with standard web API calls
 * or graceful fallback handling.
 */
export async function safeInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  void args;
  throw new Error(
    `Command "${cmd}" is not available in web mode: running pure Vite localhost app.`,
  );
}
