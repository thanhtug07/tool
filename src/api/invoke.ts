/**
 * Web API command invocation wrapper.
 *
 * All IPC commands are routed through HTTP to the Python worker.
 */

const WORKER_BASE = "http://127.0.0.1:8765";
const AUTH_TOKEN = "dev-placeholder-token";

/**
 * Tauri command -> HTTP route mapping.
 * Unlisted commands throw a clear error.
 */
const HTTP_ROUTES: Record<
  string,
  {
    method: "GET" | "POST" | "DELETE";
    path: string;
    buildRequest: (args: Record<string, unknown>) => { params?: Record<string, string>; body?: unknown };
  }
> = {
  "ping": {
    method: "GET", path: "/api/health",
    buildRequest: () => ({}),
  },
  // Projects
  "project.list": {
    method: "GET", path: "/api/projects",
    buildRequest: () => ({}),
  },
  "project.findBySourceVideo": {
    method: "GET", path: "/api/projects/by-source",
    buildRequest: ({ videoPath }) => ({ params: { video_path: String(videoPath) } }),
  },
  "project.open": {
    method: "GET", path: "/api/projects",
    buildRequest: ({ id }) => ({ params: { _pathParam: String(id) } }),
  },
  "project.create": {
    method: "POST", path: "/api/projects",
    buildRequest: ({ name, videoPath }) => ({ body: { name: String(name), videoPath: String(videoPath) } }),
  },
  "project.save": {
    method: "POST", path: "/api/projects",
    buildRequest: ({ id }) => ({ params: { _pathParam: `${String(id)}/save` }, body: {} }),
  },
  "project.delete": {
    method: "DELETE", path: "/api/projects",
    buildRequest: ({ id }) => ({ params: { _pathParam: String(id) } }),
  },
  // Settings
  "settings.get_all": {
    method: "GET", path: "/api/settings",
    buildRequest: () => ({}),
  },
  "settings.set": {
    method: "POST", path: "/api/settings",
    buildRequest: ({ key, value }) => ({ body: { key: String(key), value: String(value) } }),
  },
  // TTS
  "settings.voices": {
    method: "GET", path: "/api/tts/voices",
    buildRequest: () => ({}),
  },
  "settings.ttsPreview": {
    method: "POST", path: "/api/tts/preview",
    buildRequest: ({ engine, voice, text }) => ({ body: { engine: String(engine), voice: String(voice), text: String(text) } }),
  },
  // Jobs
  "job.list": {
    method: "GET", path: "/api/jobs",
    buildRequest: () => ({}),
  },
  "job.list_all": {
    method: "GET", path: "/api/jobs",
    buildRequest: () => ({}),
  },
  "job.get": {
    method: "GET", path: "/api/jobs",
    buildRequest: ({ jobId, id }) => ({ params: { _pathParam: String(jobId || id) } }),
  },
  // Media
  "media.probe": {
    method: "GET", path: "/api/media/probe",
    buildRequest: ({ path }) => ({ params: { path: String(path) } }),
  },
  // Worker / system
  "worker.get_worker_state": {
    method: "GET", path: "/api/worker/state",
    buildRequest: () => ({}),
  },
  "system.hardware": {
    method: "GET", path: "/api/system/hardware",
    buildRequest: () => ({}),
  },
  // Models (Settings -> Providers -> Download)
  "models.catalog": {
    method: "GET", path: "/v1/models/catalog",
    buildRequest: () => ({}),
  },
  "models.list_local": {
    method: "GET", path: "/v1/models/catalog",
    buildRequest: () => ({}),
  },
  "models.download": {
    method: "POST", path: "/v1/models/download",
    buildRequest: ({ repoId, filename, mirror, localDir }) => ({
      body: { repo_id: String(repoId), filename: String(filename), local_dir: String(localDir || ""), mirror: mirror ? String(mirror) : null },
    }),
  },
  // Providers (Settings -> AI Providers)
  "providers.list": {
    method: "GET", path: "/v1/providers/list",
    buildRequest: () => ({}),
  },
  "providers.get": {
    method: "GET", path: "/v1/providers/list",
    buildRequest: ({ id }) => ({ params: { id: String(id) } }),
  },
  "providers.test": {
    method: "POST", path: "/v1/providers/test",
    buildRequest: ({ id, apiKey }) => ({ body: { provider_kind: String(id), api_key: apiKey ? String(apiKey) : null } }),
  },
  // Job actions
  "job.cancel": {
    method: "POST", path: "/v1/jobs",
    buildRequest: ({ jobId, id }) => ({ params: { _pathParam: `${String(jobId || id)}/cancel` } }),
  },
  "job.retry": {
    method: "POST", path: "/v1/jobs",
    buildRequest: ({ jobId, id }) => ({ params: { _pathParam: `${String(jobId || id)}/retry` } }),
  },
  // Subtitle
  "subtitle.get_cues": {
    method: "GET", path: "/api/subtitle/cues",
    buildRequest: ({ projectId }) => ({ params: { project_id: String(projectId) } }),
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
  const headers: Record<string, string> = { "Authorization": `Bearer ${AUTH_TOKEN}` };
  if (route.method === "POST" || route.method === "DELETE") {
    headers["Content-Type"] = "application/json";
  }
  fetchOpts.headers = headers;
  if (body) {
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
