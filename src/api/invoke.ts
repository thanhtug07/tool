/**
 * Web API command invocation wrapper.
 *
 * All IPC commands are routed through HTTP to the Python worker.
 */

const WORKER_BASE = "http://127.0.0.1:8765";

/**
 * Tauri command -> HTTP route mapping.
 * Unlisted commands throw a clear error.
 */
const HTTP_ROUTES: Record<
  string,
  {
    method: "GET" | "POST" | "DELETE";
    path: string;
    /** Build query string params for GET, or JSON body for POST. */
    buildRequest: (args: Record<string, unknown>) => { params?: Record<string, string>; body?: unknown };
  }
> = {
  "ping": {
    method: "GET",
    path: "/api/health",
    buildRequest: () => ({}),
  },
  // Projects
  "project.list": {
    method: "GET",
    path: "/api/projects",
    buildRequest: () => ({}),
  },
  "project.findBySourceVideo": {
    method: "GET",
    path: "/api/projects/by-source",
    buildRequest: ({ videoPath }) => ({
      params: { video_path: String(videoPath) },
    }),
  },
  "project.open": {
    method: "GET",
    path: "/api/projects",
    buildRequest: ({ id }) => ({
      // path param — appended after the base path
      params: { _pathParam: String(id) },
    }),
  },
  "project.create": {
    method: "POST",
    path: "/api/projects",
    buildRequest: ({ name, videoPath }) => ({
      body: { name: String(name), videoPath: String(videoPath) },
    }),
  },
  "project.delete": {
    method: "DELETE",
    path: "/api/projects",
    buildRequest: ({ id }) => ({
      params: { _pathParam: String(id) },
    }),
  },
  "project.save": {
    method: "POST",
    path: "/api/projects",
    buildRequest: ({ id }) => ({
      params: { _pathParam: `${String(id)}/save` },
      body: {},
    }),
  },
  // Settings
  "settings.get_all": {
    method: "GET",
    path: "/api/settings",
    buildRequest: () => ({}),
  },
  "settings.set": {
    method: "POST",
    path: "/api/settings",
    buildRequest: ({ key, value }) => ({
      body: { key: String(key), value: String(value) },
    }),
  },
  // TTS
  "settings.voices": {
    method: "GET",
    path: "/api/tts/voices",
    buildRequest: () => ({}),
  },
  "settings.ttsPreview": {
    method: "POST",
    path: "/api/tts/preview",
    buildRequest: ({ engine, voice, text }) => ({
      body: { engine: String(engine), voice: String(voice), text: String(text) },
    }),
  },
  // Jobs
  "job.list": {
    method: "GET",
    path: "/api/jobs",
    buildRequest: () => ({}),
  },
  "job.get": {
    method: "GET",
    path: "/api/jobs",
    buildRequest: ({ jobId, id }) => ({
      params: { _pathParam: String(jobId || id) },
    }),
  },
  "job.list_all": {
    method: "GET",
    path: "/api/jobs",
    buildRequest: () => ({}),
  },
  // Media
  "media.probe": {
    method: "GET",
    path: "/api/media/probe",
    buildRequest: ({ path }) => ({
      params: { path: String(path) },
    }),
  },
  // Worker / system
  "worker.get_worker_state": {
    method: "GET",
    path: "/api/worker/state",
    buildRequest: () => ({}),
  },
  "system.hardware": {
    method: "GET",
    path: "/api/system/hardware",
    buildRequest: () => ({}),
  },
};

function toQueryString(params: Record<string, string>): string {
  const entries = Object.entries(params).filter(
    ([k, v]) => v !== undefined && v !== null && !k.startsWith("_"),
  );
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

async function httpInvoke<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  const route = HTTP_ROUTES[cmd];
  if (!route) {
    throw new Error(
      `Command "${cmd}" is not available through the HTTP interface.`,
    );
  }
  const { params, body } = route.buildRequest(args);

  let url = `${WORKER_BASE}${route.path}`;

  // Path params: routes like /api/projects/{id} need the ID appended
  if (params?._pathParam) {
    url += `/${encodeURIComponent(params._pathParam)}`;
  }

  // Query string for GET
  if (route.method === "GET" && params) {
    url += toQueryString(params);
  }

  const fetchOpts: RequestInit = { method: route.method };
  if (route.method === "POST" && body) {
    fetchOpts.headers = { "Content-Type": "application/json" };
    fetchOpts.body = JSON.stringify(body);
  }

  const res = await fetch(url, fetchOpts);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} — ${cmd}: ${text}`);
  }
  return (await res.json()) as T;
}

export async function safeInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  return httpInvoke<T>(cmd, args ?? {});
}
