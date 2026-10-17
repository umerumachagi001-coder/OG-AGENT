// API clients. The orchestrator (:8001) is the main backend; a few read-only views (suppliers, purchase orders,
// the action log) come straight from the mock company systems (:8000), which already allow browser access.
// Nothing here fabricates data: every function returns the server's response or throws.
// When Supabase login is on, every call to the orchestrator carries the signed-in user's access token.

import { accessToken, sessionRejected } from "./auth";

const q = new URLSearchParams(location.search);
export const API =
  q.get("api") ||
  import.meta.env.VITE_API_URL ||
  (location.port === "8001" ? location.origin : "http://127.0.0.1:8001");

export const SERVICES =
  q.get("services") ||
  import.meta.env.VITE_SERVICES_URL ||
  API.replace(/:8001\b/, ":8000");

async function req(base, method, path, body) {
  const raw = typeof Blob !== "undefined" && body instanceof Blob;   // e.g. a voice recording, sent as-is
  const headers = body ? { "Content-Type": raw ? (body.type || "application/octet-stream") : "application/json" } : {};
  if (base === API) {
    const token = await accessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  let res;
  try {
    res = await fetch(base + path, {
      method,
      headers,
      body: raw ? body : body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(`Can't reach ${base}. Is the backend running (python scripts/dev.py)?`);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (res.status === 401 && base === API) sessionRejected();
  if (!res.ok) {
    const d = data?.detail ?? data;
    // FastAPI validation errors arrive as a list: show the first message, not the raw JSON.
    const msg = typeof d === "string" ? d : Array.isArray(d) ? (d[0]?.msg || "The request was not valid") : d?.message || JSON.stringify(d);
    const err = new Error(msg || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

const o = (m, p, b) => req(API, m, p, b);
const s = (m, p, b) => req(SERVICES, m, p, b);

export const api = {
  health: () => o("GET", "/health"),
  exceptions: (history = false) => o("GET", `/exceptions${history ? "?include_history=true" : ""}`),
  exception: (id) => o("GET", `/exceptions/${id}`),
  trail: (id, after = 0) => o("GET", `/exceptions/${id}/trail?after=${after}`),
  investigate: (id) => o("POST", `/exceptions/${id}/investigate`),
  approve: (actionId, by, steps) => o("POST", `/actions/${actionId}/approve`, { by, steps }),
  reject: (actionId, by, feedback) => o("POST", `/actions/${actionId}/reject`, { by, feedback }),
  undo: (id, by, reason) => o("POST", `/exceptions/${id}/undo`, { by, reason }),
  voiceConfig: () => o("GET", "/voice/config"),
  voiceAsk: (recording) => o("POST", "/voice/ask", recording),
  voiceText: (text, context) => o("POST", "/voice/text", { text, context: context || null }),
  metrics: () => o("GET", "/metrics"),
  scenarios: () => o("GET", "/scenarios"),
  trigger: (name) => o("POST", `/trigger?fail=${encodeURIComponent(name)}`),
  reset: () => o("POST", "/reset"),
  orders: () => o("GET", "/orders?limit=500"),

  // mock company systems, read-only
  order: (id) => s("GET", `/orders/${encodeURIComponent(id)}`),
  suppliers: () => s("GET", "/suppliers"),
  supplierStatus: (params) => s("GET", `/suppliers/status?${new URLSearchParams(params)}`),
  actionLog: (limit = 200) => s("GET", `/admin/action_log?limit=${limit}`),
};
