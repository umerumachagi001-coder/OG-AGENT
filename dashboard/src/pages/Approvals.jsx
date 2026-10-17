import { useEffect, useState } from "react";
import { api } from "../api";
import DecisionPanel from "../components/DecisionPanel";
import { Chip, Empty, ErrorNote, Loading, Panel, SeverityTag } from "../components/ui";
import { VERDICT, decisions, keyFacts } from "../derive";
import { DOMAIN, ago, dateTime, rupees } from "../format";
import { useStore } from "../store";

// Approval center: every case the policy sent to a person, with the evidence needed to decide, and the decisions
// already taken (approver and time come from the recorded trail).

export default function Approvals() {
  const { exceptions, loaded } = useStore();
  const waiting = exceptions.filter((e) => e.status === "awaiting_approval").sort((a, b) => a.id - b.id);
  const decidedIds = exceptions.filter((e) => ["resolved", "rejected"].includes(e.status)).map((e) => e.id);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-cond text-[30px] font-bold leading-tight">Approval center</h1>
        <p className="max-w-[75ch] text-[14.5px] text-muted">
          Fixes the approval policy won't run on its own, such as switching a supplier or correcting a price. Approving runs the fix
          immediately; the result shown is what the server reports.
        </p>
      </header>

      {!loaded ? <Loading /> : waiting.length ? (
        <div className="space-y-6">
          {waiting.map((e) => <PendingCase key={e.id} e={e} />)}
        </div>
      ) : (
        <Empty>Nothing is waiting for approval. Trigger "Supplier misses ship date" or "Discount stacking" to see one.</Empty>
      )}

      <DecidedLog ids={decidedIds} />
    </div>
  );
}

function PendingCase({ e }) {
  const [d, setD] = useState(null);
  const [trail, setTrail] = useState([]);
  const [error, setError] = useState(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    Promise.all([api.exception(e.id), api.trail(e.id)])
      .then(([x, t]) => { if (alive) { setD(x); setTrail(t); setError(null); } })
      .catch((err) => alive && setError(err));
    return () => { alive = false; };
  }, [e.id, n, e.status]);

  const facts = d ? keyFacts(d, trail).filter((f) => f.tone !== "good") : [];
  return (
    <section aria-label={`Decision for ${e.order_id}`} className="rounded-xl bg-deck ring-1 ring-line">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-3">
        <a href={`#/case/${e.id}`} className="font-cond text-[22px] font-bold hover:underline">{e.order_id}</a>
        <SeverityTag severity={e.severity} />
        <span className="text-[14px] text-muted">{DOMAIN[e.domain]} problem, {e.customer_name} ({e.customer_tier}), {rupees(e.order_value)}</span>
        <Chip tone="warn" className="ml-auto">Pending, waiting {ago(e.created_at).replace(" ago", "")}</Chip>
      </header>
      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <div className="space-y-3">
          <p className="text-[13px] font-semibold text-signal">Assessment</p>
          <p className="font-cond text-[19px] font-semibold leading-snug">{e.root_cause || "Not recorded"}</p>
          {facts.length > 0 && (
            <ul className="space-y-2 text-[14px]">
              {facts.map((f) => (
                <li key={f.label} className="border-l-2 border-crit/60 pl-3">
                  <span className="text-muted">{f.label}: </span>{f.value}
                  <p className="text-[12.5px] text-dim">{f.detail}</p>
                </li>
              ))}
            </ul>
          )}
          {d?.evidence?.length > 0 && (
            <details className="text-[14px]">
              <summary className="cursor-pointer text-muted hover:text-ink">Evidence ({d.evidence.length})</summary>
              <ul className="mt-2 space-y-1.5">{d.evidence.map((x, i) => <li key={i} className="text-ink/90">{x.fact}</li>)}</ul>
            </details>
          )}
          <a href={`#/case/${e.id}`} className="inline-block text-[14px] text-signal hover:underline">Open full investigation</a>
        </div>
        <div>
          {error ? <ErrorNote error={error} onRetry={() => setN((x) => x + 1)} /> : !d ? <Loading /> : (
            <DecisionPanel exceptionId={e.id} actions={d.actions} compact onDone={() => setN((x) => x + 1)} />
          )}
        </div>
      </div>
    </section>
  );
}

function DecidedLog({ ids }) {
  const [rows, setRows] = useState(null);
  const key = ids.join(",");
  useEffect(() => {
    let alive = true;
    if (!ids.length) { setRows([]); return; }
    Promise.all(ids.map((id) => Promise.all([api.exception(id), api.trail(id)])))
      .then((all) => {
        if (!alive) return;
        setRows(all.flatMap(([x, t]) => decisions(t, "human").map((h) => ({ e: x, h }))).sort((a, b) => b.h.ts.localeCompare(a.h.ts)));
      })
      .catch(() => alive && setRows([]));
    return () => { alive = false; };
  }, [key]);

  return (
    <Panel title="Decisions taken" bodyClass="p-0">
      {rows == null ? <div className="p-4"><Loading /></div> : !rows.length ? (
        <div className="p-4"><Empty>No decisions have been made this session.</Empty></div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-[14px]">
            <thead className="border-b border-line text-[12.5px] text-dim">
              <tr><th className="px-4 py-2.5 font-medium">When</th><th className="px-3 py-2.5 font-medium">Order</th>
                <th className="px-3 py-2.5 font-medium">Option</th><th className="px-3 py-2.5 font-medium">Decision</th>
                <th className="px-4 py-2.5 font-medium">Approver</th></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map(({ e, h }) => (
                <tr key={`${e.id}-${h.step_no}`}>
                  <td className="px-4 py-2.5 text-muted">{dateTime(h.ts)}</td>
                  <td className="px-3 py-2.5"><a href={`#/case/${e.id}`} className="font-semibold hover:underline">{e.order_id}</a></td>
                  <td className="px-3 py-2.5">{h.content.title}{h.content.feedback ? <span className="text-dim">: {h.content.feedback}</span> : null}</td>
                  <td className="px-3 py-2.5"><Chip tone={h.content.verdict === "approved" ? "good" : h.content.verdict === "undo" ? "warn" : "muted"}>{VERDICT[h.content.verdict] || h.content.verdict}</Chip></td>
                  <td className="px-4 py-2.5">{h.content.by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
