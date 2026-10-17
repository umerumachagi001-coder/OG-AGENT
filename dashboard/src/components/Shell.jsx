import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { signOut } from "../auth";
import VoiceAssistant from "./VoiceAssistant";
import { go, useStore } from "../store";
import { Button, ConfirmDialog } from "./ui";

// Line icons, 20px, stroke = currentColor.
const I = {
  overview: <path d="M3 13h7V3H3v10Zm0 8h7v-6H3v6Zm11 0h7V11h-7v10Zm0-18v6h7V3h-7Z" />,
  exceptions: <><path d="M4 6h16M4 12h16M4 18h10" /><circle cx="19" cy="18" r="2" /></>,
  approvals: <><path d="M9 12l2 2 4-4" /><path d="M12 3l8 4v5c0 4.4-3.4 8.3-8 9-4.6-.7-8-4.6-8-9V7l8-4Z" /></>,
  workflow: <><rect x="3" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="16" width="7" height="5" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><path d="M10 5.5h4M17.5 8v8" /></>,
  audit: <><path d="M8 3h8l4 4v14H4V3h4Z" /><path d="M8 12h8M8 16h6M8 8h3" /></>,
  suppliers: <><path d="M3 21V9l6-4v4l6-4v4l6-4v16H3Z" /><path d="M8 21v-4h3v4" /></>,
};

export function Icon({ name, className = "size-5" }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      className={className} aria-hidden>{I[name]}</svg>
  );
}

export const NAV = [
  { key: "overview", label: "Overview" },
  { key: "exceptions", label: "Exceptions" },
  { key: "approvals", label: "Approvals" },
  { key: "workflow", label: "Workflow" },
  { key: "audit", label: "Audit trail" },
  { key: "suppliers", label: "Suppliers" },
];

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
      <path d="M16 3 27 8v7.5c0 6.6-4.6 12-11 13.5C9.6 27.5 5 22.1 5 15.5V8l11-5Z" fill="none" stroke="var(--color-signal)" strokeWidth="2" />
      <circle cx="16" cy="15.5" r="5.5" fill="none" stroke="var(--color-ink)" strokeWidth="1.6" />
      <circle cx="16" cy="15.5" r="1.8" fill="var(--color-warn)" />
    </svg>
  );
}

export function Sidebar({ page }) {
  const { exceptions } = useStore();
  const pending = exceptions.filter((e) => e.status === "awaiting_approval").length;
  const active = page === "case" ? "exceptions" : page;
  return (
    <nav aria-label="Main" className="flex shrink-0 border-line bg-deck md:w-56 md:flex-col md:border-r max-md:fixed max-md:inset-x-0 max-md:bottom-0 max-md:z-40 max-md:border-t">
      <a href="#/overview" className="hidden items-center gap-2.5 px-5 py-4 md:flex">
        <Logo />
        <span className="font-cond text-[19px] font-bold leading-tight">OrderGuard <span className="text-signal">AI</span></span>
      </a>
      <ul className="flex flex-1 justify-around md:mt-2 md:flex-col md:justify-start md:gap-0.5 md:px-3">
        {NAV.map((n) => {
          const on = active === n.key;
          return (
            <li key={n.key}>
              <a href={`#/${n.key}`} aria-current={on ? "page" : undefined}
                className={`relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-[14.5px] max-md:flex-col max-md:gap-1 max-md:px-2 max-md:py-2 max-md:text-[11px] ${
                  on ? "bg-signal-deep/70 text-ink" : "text-muted hover:bg-raised hover:text-ink"}`}>
                {on && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-signal max-md:hidden" aria-hidden />}
                <Icon name={n.key} className={`size-5 ${on ? "text-signal" : ""}`} />
                <span>{n.label}</span>
                {n.key === "approvals" && pending > 0 && (
                  <span className="ml-auto rounded-full bg-warn px-1.5 text-[12px] font-bold leading-5 text-night max-md:absolute max-md:right-1 max-md:top-1"
                    aria-label={`${pending} waiting`}>{pending}</span>
                )}
              </a>
            </li>
          );
        })}
      </ul>
      <EngineStatus />
    </nav>
  );
}

function EngineStatus() {
  const { health, lastSync } = useStore();
  if (!health) return null;
  const n8n = health.engine === "n8n";
  const ok = health.ok && (!n8n || health.n8n);
  return (
    <div className="hidden border-t border-line px-5 py-4 text-[12.5px] md:block">
      <p className="flex items-center gap-2 text-ink">
        <span className={`inline-block size-2 rounded-full ${ok ? "bg-good" : "bg-crit"}`} aria-hidden />
        {!health.ok ? "Backend unreachable" : n8n ? (health.n8n ? "n8n automation running" : "n8n not answering") : "Python engine running"}
      </p>
      <p className="mt-1 text-dim">
        Diagnosis by {health.agent_mode === "llm" ? "Claude" : "rule playbook"}
      </p>
      {n8n && health.n8n_editor && (
        <a href={health.n8n_editor} target="_blank" rel="noreferrer" className="mt-2 inline-block text-signal hover:underline">
          Open n8n editor
        </a>
      )}
      {lastSync && <p className="mt-2 text-dim">Updated {lastSync.toLocaleTimeString("en-IN")}</p>}
    </div>
  );
}

export function TopBar() {
  const { health, approver, setApprover, signedIn, notify, refresh, orders, exceptions } = useStore();
  const [menu, setMenu] = useState(false);
  const [scenarios, setScenarios] = useState([]);
  const [busy, setBusy] = useState(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [term, setTerm] = useState("");
  const ref = useRef(null);
  const ok = health?.ok;

  useEffect(() => {
    if (menu && !scenarios.length) api.scenarios().then(setScenarios).catch((e) => notify(e.message, "crit"));
    const close = (e) => ref.current && !ref.current.contains(e.target) && setMenu(false);
    const esc = (e) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [menu]);

  const trigger = async (s) => {
    setBusy(s.name);
    try {
      const r = await api.trigger(s.name);
      setMenu(false);
      if (r.n8n_error) notify(`Failure injected, but n8n didn't respond: ${r.n8n_error}`, "crit");
      else if (r.exceptions_created?.length) { refresh(); go(`case/${r.exceptions_created[0]}`); }
      else notify(`${s.title} is already open. Reset the demo to run it again.`);
    } catch (e) { notify(e.message, "crit"); } finally { setBusy(null); }
  };

  const reset = async () => {
    setBusy("reset");
    try { await api.reset(); setConfirmReset(false); refresh(); go("overview"); notify("Demo reset. All orders are healthy again.", "good"); }
    catch (e) { notify(e.message, "crit"); } finally { setBusy(null); }
  };

  const search = (e) => {
    e.preventDefault();
    const t = term.trim().toUpperCase();
    if (!t) return;
    const id = /^\d+$/.test(t) ? `ORD-${t}` : t.startsWith("ORD-") ? t : t;
    const exc = exceptions.find((x) => x.order_id === id);
    if (exc) { go(`case/${exc.id}`); setTerm(""); return; }
    if (orders.some((o) => o.id === id)) { go(`order/${id}`); setTerm(""); return; }
    notify(`No order called ${id}. Order IDs look like ORD-1001.`);
  };

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-night/95 px-4 py-3 backdrop-blur sm:px-6">
      <a href="#/overview" className="flex items-center gap-2 md:hidden">
        <Logo /><span className="font-cond text-[18px] font-bold">OrderGuard <span className="text-signal">AI</span></span>
      </a>
      <form onSubmit={search} role="search" className="relative min-w-[200px] flex-1 md:max-w-sm">
        <label htmlFor="order-search" className="sr-only">Find an order</label>
        <svg viewBox="0 0 24 24" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-dim" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" />
        </svg>
        <input id="order-search" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Find an order, e.g. ORD-1002"
          className="w-full rounded-lg bg-deck py-2 pl-9 pr-3 text-[14px] text-ink placeholder:text-dim ring-1 ring-line focus:ring-signal" />
      </form>

      {signedIn ? (
        <div className="ml-auto flex items-center gap-2 text-[13px] text-muted">
          <span className="hidden sm:inline">Signed in as</span>
          <span className="max-w-[12rem] truncate font-medium text-ink" title={signedIn.user?.email}>{approver}</span>
          <Button size="sm" kind="ghost" onClick={signOut}>Sign out</Button>
        </div>
      ) : (
        <label className="ml-auto flex items-center gap-2 text-[13px] text-muted">
          Approving as
          <input value={approver} onChange={(e) => setApprover(e.target.value)}
            className="w-28 rounded-lg bg-deck px-2.5 py-1.5 text-[14px] text-ink ring-1 ring-line focus:ring-signal" />
        </label>
      )}

      <VoiceAssistant />

      <div className="relative" ref={ref}>
        <Button kind="warn" onClick={() => setMenu((m) => !m)} aria-expanded={menu} aria-haspopup="menu" disabled={!ok}>
          Trigger a failure
        </Button>
        {menu && (
          <div role="menu" className="absolute right-0 z-40 mt-2 w-[min(92vw,400px)] overflow-hidden rounded-xl bg-deck shadow-[0_18px_50px_-15px_rgba(0,0,0,.8)] ring-1 ring-line-strong">
            <p className="border-b border-line px-4 py-2.5 text-[13px] text-muted">
              Breaks a healthy demo order in the mock systems. The automation detects it within seconds.
            </p>
            <ul>
              {scenarios.map((s) => (
                <li key={s.name}>
                  <button role="menuitem" onClick={() => trigger(s)} disabled={!!busy}
                    className="block w-full px-4 py-3 text-left hover:bg-raised disabled:opacity-50">
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="font-medium">{s.title}</span>
                      <span className="text-[12px] text-dim">{s.order_id}</span>
                    </span>
                    <span className="mt-0.5 block text-[13px] leading-snug text-muted">{s.summary}</span>
                  </button>
                </li>
              ))}
              {!scenarios.length && <li className="px-4 py-3 text-muted">Loading scenarios…</li>}
            </ul>
          </div>
        )}
      </div>
      <Button onClick={() => setConfirmReset(true)} disabled={!ok}>Reset demo</Button>

      <ConfirmDialog open={confirmReset} title="Reset the demo?" confirmLabel="Reset demo" confirmKind="warn"
        busy={busy === "reset"} onConfirm={reset} onCancel={() => setConfirmReset(false)}>
        Every order goes back to its healthy starting state and all live exceptions are cleared. Paused n8n runs are closed.
      </ConfirmDialog>
    </header>
  );
}
