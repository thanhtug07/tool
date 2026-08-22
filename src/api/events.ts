import type { JobLogEvent, JobStatusEvent } from "./job";
import type { ModelDownloadProgress } from "./models";
import type { TaskLogEvent, TaskProgressEvent, TaskStatusEvent } from "./task";

export type UnlistenFn = () => void;

export function onJobStatus(handler?: (event: JobStatusEvent) => void): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}

export function onJobLog(handler?: (event: JobLogEvent) => void): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}

export function onModelDownloadProgress(
  handler?: (event: ModelDownloadProgress) => void,
): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}

export function onTaskStatus(handler?: (event: TaskStatusEvent) => void): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}

export function onTaskProgress(handler?: (event: TaskProgressEvent) => void): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}

export function onTaskLog(handler?: (event: TaskLogEvent) => void): Promise<UnlistenFn> {
  void handler;
  return Promise.resolve(() => {});
}
