import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Button, Chip, Empty, ErrorNote, Loading, Panel } from "../components/ui";
import { auditEvents } from "../derive";
import { TOOL, dateTimeSec } from "../format";
import { useStore } from "../store";
import { printAuditReport } from "../auditReport";

const TYPES = { investigation: "Investigation", policy: "Policy decision", approval: "Approval / rejection", action: "Action",
  escalation: "Escalation", failure: "Failure / fallback", notification: "Notification", system: "System action log" };

const STATUS_TONE = { completed: "good", approved: "good", "auto-approved": "good", rejected: "muted", failed: "crit",
  escalated: "warn", "needs approval": "warn", fallback: "warn", corrected: "live", skipped: "muted" };

export default function Audit() {
  const { exceptions, approver } = useStore();
  const [events, setEvents] = useState(null);
  const [log, setLog] = useState(null);
  const [logError, setLogError] = useState(null);
  const [type, setType] = useState("all");
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(null);
  const [n, setN] = useState(0);
  const key = exceptions.map((e) => `${e.id}:${e.status}`).join(",");

  useEffect(() => {
    let alive = true;
    Promise.all(exceptions.map((e) => api.trail(e.id).then((t) => auditEvents(e, t))))
      .then((all) => alive && setEvents(all.flat())).catch(() => alive && setEvents([]));
    api.actionLog().then((l) => { if (alive) { setLog(l); setLogError(null); } }).catch((e) => alive && setLogError(e));
    return () => { alive = false; };
  }, [key, n]);

  const rows = useMemo(() => {
    const sys = (log || []).map((r) => {
      let params = {}, result = {};
      try { params = JSON.parse(r.params_json); result = JSON.parse(r.result_json); } catch {}
      return { ts: r.ts, type: "system", actor: "Order systems", status: r.ok ? "completed" : "failed",
        order_id: params.order_id || result.order_id || "", exception_id: null, step: `log-${r.id}`,
        text: `${TOOL[r.action_type] || r.action_type}: ${result.message || ""}`, raw: { params, result, cost: r.cost } };
    });
    const t = term.trim().toLowerCase();
    return [...(events || []), ...sys]
      .filter((r) => type === "all" || r.type === type)
      .filter((r) => !t || [r.order_id, r.actor, r.text].some((v) => String(v || "").toLowerCase().includes(t)))
      .sort((a, b) => b.ts.localeCompare(a.ts) || (typeof b.step === "number" && typeof a.step === "number" ? b.step - a.step : 0));
  }, [events, log, type, term]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-cond text-[30px] font-bold leading-tight">Audit trail</h1>
        <p className="max-w-[80ch] text-[14.5px] text-muted">
          Read-only. Case events come from each case's recorded steps; system entries come from the order systems' own action log,
          which records every change made to an order.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-[13.5px] text-muted">Event type
          <select value={type} onChange={(e) => setType(e.target.value)} className="rounded-lg bg-deck px-2.5 py-1.5 text-ink ring-1 ring-line">
            <option value="all">All</option>
            {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Search order, person, text" aria-label="Search audit events"
          className="min-w-[240px] rounded-lg bg-deck px-3 py-1.5 text-[14px] text-ink placeholder:text-dim ring-1 ring-line focus:ring-signal" />
        <Button size="sm" onClick={() => setN((x) => x + 1)} className="ml-auto">Refresh</Button>
        <Button size="sm" kind="primary" disabled={events == null || !rows.length} title="Opens the print dialog: choose “Save as PDF”"
          onClick={() => printAuditReport({ exceptions, rows, exportedBy: approver,
            filterLabel: [type === "all" ? "All event types" : TYPES[type], term.trim() && `search “${term.trim()}”`].filter(Boolean).join(", ") })}>
          Download PDF
        </Button>
      </div>
      {logError && <ErrorNote error={{ message: `System action log unavailable: ${logError.message}` }} onRetry={() => setN((x) => x + 1)} />}

      <Panel bodyClass="p-0">
        {events == null ? <div className="p-4"><Loading /></div> : !rows.length ? (
          <div className="p-4"><Empty>No audit events match. Events appear once the automation handles a case.</Empty></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-[14px]">
              <thead className="border-b border-line text-[12.5px] text-dim">
                <tr><th className="px-4 py-2.5 font-medium">Time</th><th className="px-3 py-2.5 font-medium">Order</th>
                  <th className="px-3 py-2.5 font-medium">Event</th><th className="px-3 py-2.5 font-medium">Actor</th>
                  <th className="px-3 py-2.5 font-medium">Status</th><th className="px-4 py-2.5 font-medium">Details</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => {
                  const k = `${r.exception_id}-${r.step}`;
                  return (
                    <Fragment key={k}>
                      <tr className="align-top">
                        <td className="whitespace-nowrap px-4 py-2.5 text-muted">{dateTimeSec(r.ts)}</td>
                        <td className="px-3 py-2.5">{r.exception_id ? <a href={`#/case/${r.exception_id}`} className="font-semibold hover:underline">{r.order_id}</a> : r.order_id || "—"}</td>
                        <td className="px-3 py-2.5 text-muted">{TYPES[r.type]}</td>
                        <td className="px-3 py-2.5">{r.actor}</td>
                        <td className="px-3 py-2.5"><Chip tone={STATUS_TONE[r.status] || "muted"}>{r.status}</Chip></td>
                        <td className="max-w-[460px] px-4 py-2.5">
                          <p className="leading-snug">{r.text}</p>
                          <button onClick={() => setOpen(open === k ? null : k)} className="mt-0.5 text-[12.5px] text-dim hover:text-ink" aria-expanded={open === k}>
                            {open === k ? "Hide source data" : "Show source data"}
                          </button>
                        </td>
                      </tr>
                      {open === k && (
                        <tr><td colSpan={6} className="px-4 pb-3">
                          <pre className="max-h-72 overflow-auto rounded-lg bg-night p-3 text-[12px] text-muted ring-1 ring-line">{JSON.stringify(r.raw, null, 2)}</pre>
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
