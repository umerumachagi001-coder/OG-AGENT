import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import DecisionPanel from "../components/DecisionPanel";
import { StageList } from "../components/Pipeline";
import Trail from "../components/Trail";
import { Button, Chip, ConfirmDialog, ErrorNote, Loading, Panel, RawJson, SeverityTag, StatusChip } from "../components/ui";
import { VERDICT, decisions, executedActions, keyFacts, rollback, stageStates } from "../derive";
import { DOMAIN, TOOL, TOOL_DO, ago, dateTime, humanize, rupees, shortDate, stepDetail } from "../format";
import { useStore } from "../store";

const FACT_TONE = { crit: { label: "Problem", cls: "text-crit", ring: "ring-crit/40" }, warn: { label: "Watch", cls: "text-warn", ring: "ring-warn/40" },
  good: { label: "OK", cls: "text-good", ring: "ring-line" }, muted: { label: "Info", cls: "text-muted", ring: "ring-line" } };

export default function Case({ id }) {
  const { notify, refresh } = useStore();
  const [e, setE] = useState(null);
  const [steps, setSteps] = useState([]);
  const [error, setError] = useState(null);
  const last = useRef(0);
  const identity = useRef(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    setE(null); setSteps([]); setError(null); last.current = 0; identity.current = null;
    let alive = true, busy = false;
    const tick = async () => {
      if (busy) return;
      busy = true;
      try {
        let [d, t] = await Promise.all([api.exception(id), api.trail(id, last.current)]);
        if (!alive) return;
        // After a demo reset, case numbers restart, so this id can now be a different case: start over.
        const who = `${d.order_id}|${d.created_at}`;
        if (identity.current && identity.current !== who) {
          last.current = 0;
          t = await api.trail(id, 0);
          if (!alive) return;
          setSteps([]);
        }
        identity.current = who;
        setE(d); setError(null);
        if (t.length) {
          last.current = t[t.length - 1].step_no;
          setSteps((s) => [...s, ...t.filter((x) => !s.some((y) => y.step_no === x.step_no))]);
        }
      } catch (err) {
        if (alive) setError(err);
        if (err.status === 404) clearInterval(h);   // no such case: stop asking
      } finally { busy = false; }
    };
    const h = setInterval(tick, 900);
    tick();
    return () => { alive = false; clearInterval(h); };
  }, [id, nonce]);

  if (error && !e) return <ErrorNote error={error} onRetry={() => setNonce((n) => n + 1)} />;
  if (!e) return <Loading label="Opening investigation…" />;

  const live = e.investigating || e.status === "open" || e.status === "investigating";
  const facts = keyFacts(e, steps);
  const states = stageStates(e, steps);
  const policy = decisions(steps, "policy").at(-1)?.content;
  const people = decisions(steps, "human");
  const executed = executedActions(steps);
  const undone = rollback(steps);
  const reinvestigate = async () => {
    try { await api.investigate(id); notify("Investigation restarted.", "good"); refresh(); }
    catch (err) { notify(`Couldn't restart: ${err.message}`, "crit"); }
  };

  return (
    <article className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-[13px] text-dim">
        <a href="#/exceptions" className="hover:text-ink">Exceptions</a> <span aria-hidden>/</span> <span className="text-muted">{e.order_id}</span>
      </nav>

      <header className="rounded-xl bg-deck p-5 ring-1 ring-line">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="font-cond text-[30px] font-bold leading-none">{e.order_id}</h1>
          <StatusChip status={e.status} investigating={e.investigating} />
          <SeverityTag severity={e.severity} />
          <span className="text-[14px] text-muted">{DOMAIN[e.domain]} problem</span>
          <span className="text-[14px] text-dim">Detected {ago(e.created_at)}, case #{e.id}</span>
          {e.order && (
            <span className="ml-auto text-[14px] text-muted">
              {e.order.customer.name} ({e.order.customer.tier}), {rupees(e.order.total, true)}
            </span>
          )}
        </div>
        <p className="mt-3 text-[13px] font-semibold text-data">What was detected</p>
        <p className="max-w-[80ch] text-[15px] text-ink/90">{e.symptom}</p>
        <p className="mt-3 text-[13px] font-semibold text-signal">
          Assessment{e.confidence != null ? `, ${Math.round(e.confidence * 100)}% confidence` : ""}
        </p>
        <h2 className={`max-w-[40ch] font-cond font-bold leading-[1.12] ${(e.root_cause || "").length > 110 ? "text-[24px]" : "text-[28px]"}`}>
          {e.root_cause || (live ? "Finding the root cause…" : "No root cause recorded")}
        </h2>
      </header>

      {facts.length > 0 && (
        <section aria-label="Key facts" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {facts.map((f) => {
            const t = FACT_TONE[f.tone] || FACT_TONE.muted;
            return (
              <div key={f.label} className={`rounded-xl bg-deck p-3.5 ring-1 ${t.ring}`}>
                <p className="flex items-center justify-between text-[12.5px]">
                  <span className="text-muted">{f.label}</span>
                  <span className={`font-semibold ${t.cls}`}>{t.label}</span>
                </p>
                <p className="mt-1 font-medium leading-snug">{f.value}</p>
                <p className="mt-1 text-[12.5px] leading-snug text-dim">{f.detail}</p>
                {f.source && <p className="mt-1 text-[11.5px] text-dim">Source: {(TOOL[f.source] || f.source).toLowerCase()}</p>}
              </div>
            );
          })}
        </section>
      )}

      {e.status === "awaiting_approval" && undone && (
        <section className="rounded-xl bg-deck p-4 ring-1 ring-warn/40">
          <h3 className="font-cond text-[20px] font-semibold text-warn">The automatic fix was undone</h3>
          <p className="mt-1 text-[14px] text-muted">
            {(undone.content.steps || []).join(". ")}{undone.content.cost_reversed ? `. ${rupees(undone.content.cost_reversed)} reversed` : ""}.
            {" "}Undone by {undone.content.by} at {dateTime(undone.ts)}. Choose what happens next below.
          </p>
        </section>
      )}
      {e.status === "awaiting_approval" && (
        <DecisionPanel exceptionId={e.id} actions={e.actions} />
      )}
      <Outcome e={e} executed={executed} onChanged={() => { refresh(); }} />
      {e.status === "failed" && (
        <section role="alert" className="flex flex-wrap items-center gap-3 rounded-xl bg-crit-wash p-4 ring-1 ring-crit/40">
          <p className="font-cond text-[19px] font-semibold text-crit">The automation stopped before finishing this case</p>
          <Button kind="quiet" onClick={reinvestigate} className="ml-auto">Investigate again</Button>
        </section>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel title="Investigation timeline"
          action={<span className="text-[12px] text-dim">Observed facts, reasoning, assessment, policy, actions</span>}>
          <Trail steps={steps} live={live} />
        </Panel>

        <div className="space-y-5">
          <Panel title="Workflow progress"><StageList states={states} />
            <p className="mt-3 text-[12px] text-dim">Derived from this case's recorded steps. <a href="#/workflow" className="text-signal hover:underline">Pipeline view</a></p>
          </Panel>

          {e.evidence?.length > 0 && (
            <Panel title="Evidence behind the assessment">
              <ul className="space-y-2.5 text-[14px]">
                {e.evidence.map((x, i) => (
                  <li key={i} className="border-l-2 border-data/60 pl-3">
                    <p>{x.fact}</p>
                    <p className="text-[12px] text-dim">Source: {(TOOL[x.source] || x.source).toLowerCase()}</p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {policy && (
            <Panel title="Policy findings">
              <ul className="space-y-2 text-[14px]">
                {policy.options.map((o) => (
                  <li key={o.rank}>
                    <p className="font-medium">{o.title}</p>
                    <p className={o.auto ? "text-good" : "text-warn"}>{o.auto ? "Allowed to run automatically" : `Needs a person: ${humanize(o.reasons.join("; "))}`}</p>
                  </li>
                ))}
              </ul>
              {policy.context && (
                <p className="mt-3 text-[12.5px] text-dim">
                  Checked against: {policy.context.customer_tier} customer, order {rupees(policy.context.order_value)}, confidence {Math.round((policy.context.confidence || 0) * 100)}%
                  {policy.context.fraud_score != null ? `, fraud score ${policy.context.fraud_score}` : ""}.
                </p>
              )}
            </Panel>
          )}

          <Panel title="Actions executed">
            {executed.length ? (
              <ul className="space-y-2 text-[14px]">
                {executed.map((a, i) => (
                  <li key={i}>
                    <p className={a.ok ? "" : "text-crit"}>{a.ok ? "✓" : "✕"} {a.label}
                      {a.cost > 0 ? `, ${rupees(a.cost)}` : a.cost < 0 ? `, ${rupees(-a.cost)} reversed` : ""}</p>
                    <p className="text-[12.5px] text-dim">{a.message} ({dateTime(a.ts)})</p>
                  </li>
                ))}
              </ul>
            ) : <p className="text-[14px] text-muted">Nothing has been changed in the systems yet.</p>}
          </Panel>

          <Panel title="Decision history">
            {people.length || e.resolution ? (
              <ul className="space-y-2 text-[14px]">
                {people.map((p) => (
                  <li key={p.step_no}>
                    <p>{VERDICT[p.content.verdict] || p.content.verdict} by <span className="font-medium">{p.content.by}</span></p>
                    <p className="text-[12.5px] text-dim">{p.content.title}{p.content.feedback ? `: ${p.content.feedback}` : ""}. {dateTime(p.ts)}</p>
                  </li>
                ))}
                {e.resolution && (
                  <li className="text-[13px] text-muted">
                    Closed as {e.resolution.outcome} by {e.resolution.resolved_by}
                    {e.resolution.minutes_to_resolve != null ? `, ${e.resolution.minutes_to_resolve < 1 ? `${Math.max(1, Math.round(e.resolution.minutes_to_resolve * 60))} s` : `${e.resolution.minutes_to_resolve} min`} after detection` : ""}.
                  </li>
                )}
              </ul>
            ) : <p className="text-[14px] text-muted">No decisions recorded yet.</p>}
          </Panel>

          {e.customer_message && (
            <Panel title="Draft message to the customer">
              <blockquote className="rounded-lg bg-raised p-3 text-[14px] leading-snug">{e.customer_message}</blockquote>
              <p className="mt-2 text-[12px] text-dim">A draft. It's only sent if an option that emails the customer runs.</p>
            </Panel>
          )}

          {e.order && (
            <Panel title="Order record">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[14px]">
                <dt className="text-muted">Customer</dt><dd>{e.order.customer.name}, {e.order.customer.tier}, {e.order.customer.city}</dd>
                {e.order.items.map((it) => (
                  <FragmentRow key={it.sku} label="Item" value={`${it.qty} × ${it.name} at ${rupees(it.unit_price)}`} />
                ))}
                {e.order.promo_codes.length > 0 && <FragmentRow label="Offers" value={e.order.promo_codes.join(", ")} />}
                <FragmentRow label="Charged" value={rupees(e.order.total, true)} />
                <FragmentRow label="Status now" value={e.order.status.replaceAll("_", " ")} />
                {e.order.warehouse_id && <FragmentRow label="Ships from" value={e.order.warehouse_id} />}
                <FragmentRow label="Promised by" value={shortDate(e.order.promised_date)} />
                {e.order.purchase_orders?.map((p) => (
                  <FragmentRow key={p.id} label="Purchase order" value={`${p.id}, ${p.supplier_id}, ${p.status}`} />
                ))}
              </dl>
              <RawJson data={e.order} label="Show full order record" />
              <RawJson data={{ ...e, order: undefined }} label="Show case record" />
            </Panel>
          )}
        </div>
      </div>
    </article>
  );
}

function FragmentRow({ label, value }) {
  return (<><dt className="text-muted">{label}</dt><dd>{value}</dd></>);
}

function Outcome({ e, executed, onChanged }) {
  const done = e.actions.find((a) => a.status === "executed");
  const r = e.resolution;
  if (e.status === "rejected") {
    return (
      <section className="rounded-xl bg-deck p-4 ring-1 ring-line">
        <h3 className="font-cond text-[20px] font-semibold">Closed without action</h3>
        <p className="mt-1 text-[14px] text-muted">Every option was rejected{r?.human_feedback ? `: ${r.human_feedback}` : "."}</p>
      </section>
    );
  }
  if (!done || !["auto_resolved", "resolved"].includes(e.status)) return null;
  const byAgent = e.status === "auto_resolved";
  return (
    <section className="rounded-xl bg-good-wash p-4 ring-1 ring-good/40">
      <h3 className="font-cond text-[21px] font-bold text-good">
        {byAgent ? "Fixed automatically. The policy allowed it, so no one had to step in." : `Fixed after ${r?.resolved_by || "a person"} approved it`}
      </h3>
      <p className="mt-1 text-[15px]">{done.params.title}{done.cost ? `, ${rupees(done.cost)}` : ""}.</p>
      <p className="mt-0.5 text-[14px] text-muted">
        {done.params.steps.map((s) => `${TOOL_DO[s.type]}${stepDetail(s) ? ` (${stepDetail(s)})` : ""}`).join(", then ")}.
      </p>
      {executed.some((a) => !a.ok && a.tool !== "undo") && <Chip tone="crit" className="mt-2">One step failed before the final fix. See the timeline.</Chip>}
      {byAgent && <UndoControl e={e} onChanged={onChanged} />}
    </section>
  );
}

const undoing = new Set();   // case ids with an undo being sent

/** FR-15: reverse an automatic fix. Shown only when the server says it can be undone; reports only what the
 *  server confirms. */
function UndoControl({ e, onChanged }) {
  const { approver, notify } = useStore();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const plan = e.undo;
  if (!plan) return null;
  if (!plan.possible) {
    return plan.reason ? <p className="mt-2 text-[13px] text-dim">{plan.reason}.</p> : null;
  }
  const send = async () => {
    if (undoing.has(e.id)) return;
    undoing.add(e.id); setBusy(true);
    try {
      const r = await api.undo(e.id, approver, reason.trim() || undefined);
      if (r.status === "awaiting_approval") notify(`Undone by ${approver}. The order is back to how it was; the case now needs a decision.`, "good");
      else notify(`Undo ${r.status === "partial" ? "only partly worked" : "failed"}: ${r.error}. See the timeline.`, "crit");
      setOpen(false); onChanged?.();
    } catch (err) {
      notify(`Undo not done: ${err.message}`, "crit");
    } finally {
      undoing.delete(e.id); setBusy(false);
    }
  };
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-good/25 pt-3">
      <p className="text-[13px] text-muted">Disagree with what the agent did? You can reverse it and decide yourself.</p>
      <Button kind="warn" size="sm" onClick={() => setOpen(true)} className="ml-auto">Undo this fix</Button>
      <ConfirmDialog open={open} title="Undo the automatic fix?" confirmLabel="Undo the fix" confirmKind="warn" busy={busy}
        busyLabel="Undoing…" onConfirm={send} onCancel={() => setOpen(false)}>
        <p>These changes will be reversed in the order systems, newest first:</p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-ink">
          {plan.steps.map((s, i) => (
            <li key={i}><span className="font-medium">Reverse "{TOOL[s.action] || s.action}"</span>
              <span className="text-muted">: {s.message}{s.cost ? `. ${rupees(s.cost)} cost reversed` : ""}</span></li>
          ))}
        </ul>
        <p className="mt-2">The case then comes back to you to approve or reject. Undo is recorded as {approver}.</p>
        <label className="mt-3 block text-[13px] text-muted" htmlFor="undo-reason">Reason (optional, saved in the audit trail)</label>
        <input id="undo-reason" value={reason} onChange={(ev) => setReason(ev.target.value)} disabled={busy}
          className="mt-1 w-full rounded-md bg-night px-3 py-2 text-[14px] text-ink ring-1 ring-line focus:outline-none focus:ring-signal" />
      </ConfirmDialog>
    </div>
  );
}
