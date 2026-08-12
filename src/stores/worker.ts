import { useSyncExternalStore } from "react";

import { getHardware, type HardwareProfile } from "@/api/system";
import {
  getWorkerState,
  restartWorker as restartWorkerApi,
  type WorkerStateInfo,
} from "@/api/worker";

export type WorkerSnapshot = {
  info: WorkerStateInfo | null;
  hardware: HardwareProfile | null;
};

const POLL_INTERVAL_MS = 3000;

// ---- module-level store: one polling loop shared by Sidebar / Dashboard / ---
// ---- Automation so they can never show different worker states.       ----

let snapshot: WorkerSnapshot = { info: null, hardware: null };
let started = false;
let error: string | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

async function poll() {
  try {
    const [info, hardware] = await Promise.all([getWorkerState(), getHardware()]);
    snapshot = { info, hardware };
    error = null;
  } catch (e) {
    error = String(e);
  }
  emit();
}

function ensureStarted() {
  if (started) return;
  started = true;
  void poll();
  window.setInterval(() => void poll(), POLL_INTERVAL_MS);
}

function subscribe(listener: () => void): () => void {
  ensureStarted();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): WorkerSnapshot {
  return snapshot;
}

/**
 * Shared worker lifecycle + hardware snapshot. One module-level polling loop
 * feeds every consumer, so the Sidebar dot, the Dashboard card and the
 * Automation error banner always agree.
 */
export function useWorker(): WorkerSnapshot & { error: string | null } {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { ...state, error };
}

/** Stop + restart the Python sidecar, then refresh the shared snapshot. */
export async function restartWorker(): Promise<WorkerStateInfo> {
  const info = await restartWorkerApi();
  snapshot = { ...snapshot, info };
  emit();
  return info;
}
