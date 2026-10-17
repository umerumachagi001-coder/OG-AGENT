import { useEffect, useState } from "react";
import { API } from "./api";
import { currentSession, loadAuthConfig, onAuthChange, sessionEndedReason } from "./auth";
import Approvals from "./pages/Approvals";
import SignIn from "./pages/SignIn";
import Audit from "./pages/Audit";
import Case from "./pages/Case";
import Exceptions from "./pages/Exceptions";
import Order from "./pages/Order";
import Overview from "./pages/Overview";
import Suppliers from "./pages/Suppliers";
import Workflow from "./pages/Workflow";
import { Sidebar, TopBar } from "./components/Shell";
import { Button, Toast } from "./components/ui";
import { StoreProvider, useRoute, useStore } from "./store";

function Offline() {
  const { health, healthError, refresh } = useStore();
  if (!health || health.ok) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 border-b border-crit/40 bg-crit-wash px-4 py-2.5 text-[14px] sm:px-6">
      <span className="font-semibold text-crit">The backend isn't answering.</span>
      <span className="text-ink/80">
        {healthError?.message || "Start it with python scripts/dev.py."} The screen shows the last data received.
      </span>
      <Button size="sm" onClick={refresh} className="ml-auto">Retry now</Button>
    </div>
  );
}

function NotFound({ what }) {
  return (
    <div className="rounded-xl bg-deck p-6 ring-1 ring-line">
      <p className="font-cond text-[22px] font-bold">There's no {what}</p>
      <p className="mt-1 text-[14.5px] text-muted">Check the link, or find the order with the search box above.</p>
      <a href="#/exceptions" className="mt-3 inline-block text-signal hover:underline">All exceptions</a>
    </div>
  );
}

function Page() {
  const { page, id, query } = useRoute();
  switch (page) {
    case "case":
      if (!/^\d+$/.test(id || "")) return <NotFound what={`case "${id ?? ""}"`} />;
      return <Case key={id} id={Number(id)} />;
    case "order": return <Order key={id} id={id} />;
    case "exceptions": return <Exceptions query={query} />;
    case "approvals": return <Approvals />;
    case "workflow": return <Workflow />;
    case "audit": return <Audit />;
    case "suppliers": return <Suppliers />;
    default: return <Overview />;
  }
}

function Layout() {
  const { page } = useRoute();
  const { toast } = useStore();
  return (
    <div className="flex h-full max-md:flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-signal focus:px-3 focus:py-2 focus:text-night">
        Skip to content
      </a>
      <Sidebar page={page} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <Offline />
        <main id="main" className="min-h-0 flex-1 overflow-y-auto px-4 pb-24 pt-5 sm:px-6 md:pb-10">
          <div className="mx-auto max-w-[1400px]"><Page /></div>
        </main>
      </div>
      <Toast toast={toast} />
    </div>
  );
}

// With Supabase login on, nothing (not even the data poller) runs until someone is signed in.
function AuthGate({ children }) {
  const [phase, setPhase] = useState("loading");   // loading | ready | error
  const [cfg, setCfg] = useState(null);
  const [session, setSession] = useState(currentSession());
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    loadAuthConfig(API)
      .then((c) => { if (alive) { setCfg(c); setSession(currentSession()); setPhase("ready"); } })
      .catch(() => { if (alive) setPhase("error"); });
    return () => { alive = false; };
  }, [attempt]);
  useEffect(() => onAuthChange(setSession), []);
  useEffect(() => {   // backend not up yet (e.g. still starting): try again every few seconds
    if (phase !== "error") return;
    const h = setTimeout(() => setAttempt((n) => n + 1), 3000);
    return () => clearTimeout(h);
  }, [phase, attempt]);

  if (phase === "loading") return null;
  if (phase === "error") {
    return (
      <main className="flex min-h-full items-center justify-center bg-night p-6 text-center">
        <div role="alert">
          <p className="font-cond text-[24px] font-bold">The backend isn't answering yet</p>
          <p className="mt-1 text-[14.5px] text-muted">Start it with python scripts/dev.py. Retrying automatically…</p>
          <Button className="mt-4" onClick={() => setAttempt((n) => n + 1)}>Retry now</Button>
        </div>
      </main>
    );
  }
  if (cfg.auth_enabled && !session) return <SignIn expired={sessionEndedReason() === "expired"} />;
  return children;
}

export default function App() {
  return <AuthGate><StoreProvider><Layout /></StoreProvider></AuthGate>;
}
