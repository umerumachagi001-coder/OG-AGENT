// Supabase sign-in for the dashboard, using Supabase Auth's REST API directly (no extra library).
// The backend tells us whether login is on and gives the project URL + publishable key (public by design);
// the secret key never reaches the browser. The session is kept in localStorage so a reload stays signed in.

const KEY = "oea.session";
let cfg = { auth_enabled: false, url: null, publishable_key: null };
let session = load();
let ended = null;          // why the last session ended: "expired" | "signout" | null
const listeners = new Set();

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; }
}
function save(s) {
  session = s;
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch {}
  listeners.forEach((f) => f(s));
}

export const authEnabled = () => cfg.auth_enabled;
export const sessionEndedReason = () => ended;
export const currentSession = () => session;
export const onAuthChange = (f) => { listeners.add(f); return () => listeners.delete(f); };

export const displayName = (user) =>
  user?.user_metadata?.full_name || user?.user_metadata?.name || user?.email || "Signed-in user";

/** Ask the backend whether login is on. Must run before the app renders. */
export async function loadAuthConfig(apiBase) {
  const r = await fetch(`${apiBase}/auth/config`);
  if (!r.ok) throw new Error(`Backend answered ${r.status} for /auth/config`);
  cfg = await r.json();
  if (!cfg.auth_enabled) save(null);
  return cfg;
}

async function tokenRequest(grant, body) {
  let r;
  try {
    r = await fetch(`${cfg.url}/auth/v1/token?grant_type=${grant}`, {
      method: "POST",
      headers: { apikey: cfg.publishable_key, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Can't reach Supabase. Check your internet connection.");
  }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error_description || d.msg || d.message || `Sign-in failed (${r.status})`);
  return {
    access_token: d.access_token,
    refresh_token: d.refresh_token,
    expires_at: Date.now() + (d.expires_in || 3600) * 1000,
    user: d.user,
  };
}

export async function signIn(email, password) {
  const s = await tokenRequest("password", { email, password });
  ended = null;
  save(s);
  return session;
}

export async function signOut() {
  const s = session;
  ended = "signout";
  save(null);
  if (s && cfg.url) {
    fetch(`${cfg.url}/auth/v1/logout`, { method: "POST", headers: { apikey: cfg.publishable_key, Authorization: `Bearer ${s.access_token}` } })
      .catch(() => {});
  }
}

let refreshing = null;
/** A valid access token, refreshed shortly before it expires. Null when signed out. */
export async function accessToken() {
  if (!cfg.auth_enabled || !session) return null;
  if (session.expires_at - Date.now() > 60_000) return session.access_token;
  refreshing ||= tokenRequest("refresh_token", { refresh_token: session.refresh_token })
    .then((s) => { save(s); return s.access_token; })
    .catch(() => { ended = "expired"; save(null); return null; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

/** The backend refused our token (expired or revoked): back to the sign-in screen. */
export function sessionRejected() {
  if (session) { ended = "expired"; save(null); }
}
