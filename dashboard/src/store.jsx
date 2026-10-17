import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { authEnabled, currentSession, displayName } from "./auth";

// One poller for the data every screen shares, so pages don't each hammer the backend.
const Ctx = createContext(null);
export const useStore = () => useContext(Ctx);

function readName() {
  try { return localStorage.getItem("oea.approver") || "Ops lead"; } catch { return "Ops lead"; }
}

export function StoreProvider({ children }) {
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [exceptions, setExceptions] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [orders, setOrders] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [lastSync, setLastSync] = useState(null);
  const [approver, setApproverState] = useState(readName);
  const [toast, setToast] = useState(null);
  const busy = useRef(false);
  const tick = useRef(0);

  const notify = useCallback((text, tone = "muted") => setToast({ text, tone, at: Date.now() }), []);
  const setApprover = (v) => { setApproverState(v); try { localStorage.setItem("oea.approver", v); } catch {} };

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const withOrders = tick.current++ % 3 === 0;
      const [h, list, m, ords] = await Promise.all([
        api.health(), api.exceptions(), api.metrics(), withOrders ? api.orders() : Promise.resolve(null),
      ]);
      setHealth(h); setExceptions(list); setMetrics(m);
      if (ords) setOrders(ords);
      setHealthError(null); setLoaded(true); setLastSync(new Date());
    } catch (e) {
      setHealthError(e);
      setHealth((h) => (h ? { ...h, ok: false } : { ok: false }));
    } finally {
      busy.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    const h = setInterval(refresh, 1500);
    return () => clearInterval(h);
  }, [refresh]);

  useEffect(() => {
    if (!toast) return;
    const h = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(h);
  }, [toast]);

  // With Supabase login on, decisions are made as the signed-in person (the backend records the same name).
  const signedIn = authEnabled() ? currentSession() : null;
  const actingAs = signedIn ? displayName(signedIn.user) : approver;
  const value = { health, healthError, exceptions, metrics, orders, loaded, lastSync, refresh, approver: actingAs, setApprover,
    signedIn, toast, notify };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// ───────── tiny hash router: #/overview, #/case/12 … ─────────

const parse = () => {
  const [path, qs = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = (path || "overview").split("/");
  return { page: parts[0] || "overview", id: parts[1] ? decodeURIComponent(parts[1]) : null, query: new URLSearchParams(qs) };
};

export function useRoute() {
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => { setRoute(parse()); window.scrollTo?.(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (path) => { location.hash = `#/${path}`; };
