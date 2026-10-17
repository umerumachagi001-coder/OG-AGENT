import { useState } from "react";
import { api } from "../api";
import { STEP_PARAM, TOOL_DO, humanize, rupees, stepDetail } from "../format";
import { useStore } from "../store";
import { Button, Chip, ConfirmDialog } from "./ui";

const RISK = { low: "Low risk", medium: "Medium risk", high: "High risk" };
const inFlight = new Set();   // exception ids with a decision being sent, shared across screens

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Send a decision and report only what the server confirms. In n8n mode the orchestrator answers "resuming";
 *  we then watch the case until n8n has actually run the fix (or failed). */
export function useDecision(exceptionId, onDone) {
  const { approver, notify, refresh } = useStore();
  const [phase, setPhase] = useState(null); // null | sending | handing | done
  const busy = phase === "sending" || phase === "handing";

  const finish = (msg, tone) => { setPhase("done"); notify(msg, tone); refresh(); onDone?.(); };

  const approve = async (action, steps) => {
    if (inFlight.has(exceptionId)) return;
    inFlight.add(exceptionId);
    setPhase("sending");
    try {
      const r = await api.approve(action.id, approver, steps || undefined);
      if (r.skipped) return finish(`That option was already ${r.status}. The view has been refreshed.`, "warn");
      if (r.status === "resolved") return finish(`Approved by ${approver}. The fix ran and the case is resolved.`, "good");
      if (r.status === "failed") {
        const why = (r.results || []).find((x) => !x.ok)?.message;
        return finish(`Approved, but the fix failed${why ? `: ${why}` : ""}. The case is still open.`, "crit");
      }
      if (r.status === "resuming") {
        setPhase("handing");
        for (let i = 0; i < 40; i++) {
          await sleep(1000);
          const e = await api.exception(exceptionId);
          const act = e.actions.find((a) => a.id === action.id);
          if (e.status === "resolved") return finish(`Approved by ${approver}. n8n ran the fix and the case is resolved.`, "good");
          if (act?.status === "failed") {
            const trail = await api.trail(exceptionId).catch(() => []);
            const why = [...trail].reverse().find((x) => x.type === "tool_result" && !x.content.ok)?.content.error;
            return finish(`Approved, but the fix failed when n8n ran it${why ? `: ${why}` : ". See the timeline"}. Nothing was changed.`, "crit");
          }
        }
        return finish("Approval sent to n8n, but no result yet. Check the n8n editor.", "warn");
      }
      finish(`Server answered "${r.status}".`, "warn");
    } catch (e) {
      finish(`Approval not recorded: ${e.message}`, "crit");
    } finally {
      inFlight.delete(exceptionId);
    }
  };

  const reject = async (action, feedback) => {
    if (inFlight.has(exceptionId)) return;
    inFlight.add(exceptionId);
    setPhase("sending");
    try {
      const r = await api.reject(action.id, approver, feedback || undefined);
      if (r.skipped) return finish(`That option was already ${r.status === "executed" ? "carried out" : r.status}. The view has been refreshed.`, "warn");
      finish(r.remaining_options
        ? `Rejected "${action.params.title}". ${r.remaining_options} option${r.remaining_options === 1 ? "" : "s"} left.`
        : "Rejected. No options left, so the case is closed without action.", "muted");
    } catch (e) {
      finish(`Rejection not recorded: ${e.message}`, "crit");
    } finally {
      inFlight.delete(exceptionId);
    }
  };

  return { approve, reject, busy, phase };
}

function StepEditor({ steps, setSteps }) {
  const edit = (i, k, v) =>
    setSteps(steps.map((s, j) => (j === i ? { ...s, params: { ...s.params, [k]: k === "price" ? Number(v) : v } } : s)));
  return (
    <div className="mt-3 space-y-3 rounded-lg bg-night/60 p-3 ring-1 ring-line">
      {steps.map((s, i) => (
        <fieldset key={i}>
          <legend className="text-[13px] font-medium">{TOOL_DO[s.type] || s.type}</legend>
          <div className="mt-1 grid gap-2 sm:grid-cols-2">
            {Object.entries(s.params).filter(([k]) => k !== "order_id" && !Array.isArray(s.params[k])).map(([k, v]) => (
              <label key={k} className={`text-[12px] text-muted ${k === "message" ? "sm:col-span-2" : ""}`}>
                {STEP_PARAM[k] || k}
                {k === "message" ? (
                  <textarea rows={3} value={v} onChange={(e) => edit(i, k, e.target.value)}
                    className="mt-0.5 block w-full rounded-md bg-raised px-2 py-1.5 text-[14px] text-ink ring-1 ring-line focus:ring-signal" />
                ) : (
                  <input value={v} onChange={(e) => edit(i, k, e.target.value)} type={k === "price" ? "number" : "text"} step="0.01"
                    className="mt-0.5 block w-full rounded-md bg-raised px-2 py-1.5 text-[14px] text-ink ring-1 ring-line focus:ring-signal" />
                )}
              </label>
            ))}
          </div>
        </fieldset>
      ))}
    </div>
  );
}

function Option({ a, recommended, decision, compact }) {
  const p = a.params;
  const [editing, setEditing] = useState(false);
  const [steps, setSteps] = useState(p.steps);
  const [ask, setAsk] = useState(null); // "approve" | "reject"
  const [why, setWhy] = useState("");
  // A failed attempt stays open: the person can correct the values (e.g. a mistyped price) and approve again.
  const failed = a.status === "failed";
  const closed = a.status !== "proposed" && !failed;
  const editable = p.steps.some((s) => Object.entries(s.params || {}).some(([k, v]) => k !== "order_id" && !Array.isArray(v)));
  const { busy } = decision;

  return (
    <li className={`rounded-xl bg-deck p-4 ring-1 ${recommended && !closed ? "ring-signal/60" : "ring-line"} ${closed ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h4 className="font-cond text-[18px] font-semibold leading-tight">
          {p.title}
          {recommended && !closed && <span className="ml-2 align-middle text-[12.5px] font-medium text-signal">Recommended</span>}
        </h4>
        <span className="text-[14px]">
          <span className="font-cond text-[18px] font-semibold">{a.cost ? rupees(a.cost) : "No extra cost"}</span>
          <span className="ml-2 text-muted">{RISK[a.risk] || a.risk}</span>
        </span>
      </div>
      {p.impact && <p className="mt-1 text-[14px]">Expected result: {p.impact}</p>}
      {a.rationale && !compact && <p className="mt-1 max-w-[72ch] text-[14px] text-muted">Why: {a.rationale}</p>}

      <ol className="mt-2.5 space-y-1 text-[14px]">
        {p.steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-dim">{i + 1}.</span>
            <span>{TOOL_DO[s.type] || s.type}{stepDetail(s) && <span className="text-muted">: {stepDetail(s)}</span>}</span>
          </li>
        ))}
      </ol>

      {a.requires_approval === 1 && p.policy?.reasons?.length > 0 && !closed && (
        <p className="mt-2.5 text-[13px] text-warn">Policy requires a person: {humanize(p.policy.reasons.join("; "))}.</p>
      )}
      {closed && <div className="mt-2.5"><Chip tone={a.status === "executed" ? "good" : a.status === "failed" ? "crit" : "muted"}>
        {a.status === "executed" ? "Ran" : a.status === "rejected" ? "Rejected" : a.status === "failed" ? "Failed" : a.status}
      </Chip></div>}

      {failed && (
        <p role="note" className="mt-2.5 rounded-lg bg-crit-wash px-3 py-2 text-[13.5px] text-crit ring-1 ring-crit/40">
          The last attempt failed and nothing was changed{editable ? ". Correct the values with Edit first and approve again, or reject this option." : ". You can try again or reject this option."} The reason is in the timeline.
        </p>
      )}
      {!closed && editing && <StepEditor steps={steps} setSteps={setSteps} />}

      {!closed && (
        <div className="mt-3.5 flex flex-wrap gap-2">
          <Button kind="approve" disabled={busy} onClick={() => setAsk("approve")}>
            {decision.phase === "handing" ? "n8n is running the fix…" : busy ? "Sending…" : editing ? "Approve with edits" : "Approve"}
          </Button>
          {editable && (
            <Button onClick={() => { setEditing((e) => !e); setSteps(p.steps); }} disabled={busy}>
              {editing ? "Discard edits" : "Edit first"}
            </Button>
          )}
          <Button kind="danger" onClick={() => setAsk("reject")} disabled={busy}>Reject</Button>
        </div>
      )}

      <ConfirmDialog open={ask === "approve"} title={`Approve "${p.title}"?`} confirmLabel="Approve and run" confirmKind="approve"
        busy={busy} busyLabel={decision.phase === "handing" ? "n8n is running the fix…" : "Sending approval…"} onCancel={() => setAsk(null)}
        onConfirm={async () => { await decision.approve(a, editing ? steps : null); setAsk(null); }}>
        <p>This runs straight away against the live systems:</p>
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-ink">
          {(editing ? steps : p.steps).map((s, i) => <li key={i}>{TOOL_DO[s.type]}{stepDetail(s) ? `: ${stepDetail(s)}` : ""}</li>)}
        </ul>
        <p className="mt-2">Cost: {a.cost ? rupees(a.cost) : "no extra cost"}. Your name is recorded in the audit trail.</p>
      </ConfirmDialog>
      <ConfirmDialog open={ask === "reject"} title={`Reject "${p.title}"?`} confirmLabel="Reject option" confirmKind="danger"
        busy={busy} onCancel={() => setAsk(null)}
        onConfirm={async () => { await decision.reject(a, why); setAsk(null); setWhy(""); }}>
        <label className="block">
          Reason (recorded with the decision)
          <input autoFocus value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. too expensive for this customer"
            className="mt-1 block w-full rounded-md bg-raised px-2.5 py-2 text-[14px] text-ink ring-1 ring-line focus:ring-signal" />
        </label>
      </ConfirmDialog>
    </li>
  );
}

/** The decision for one exception. Shown on the investigation screen and in the approval center. */
export default function DecisionPanel({ exceptionId, actions, compact, onDone }) {
  const decision = useDecision(exceptionId, onDone);
  return (
    <section aria-label="Your decision" className="overflow-hidden rounded-xl bg-warn-wash/40 ring-1 ring-warn/50">
      <div className="hazard-edge h-1.5" aria-hidden />
      <div className="p-4 sm:p-5">
        {!compact && (
          <>
            <h3 className="font-cond text-[21px] font-bold leading-tight text-warn">This needs your decision</h3>
            <p className="mt-0.5 max-w-[70ch] text-[14px] text-muted">
              The investigation is done. The approval policy won't run these fixes without a person. Nothing happens until you approve.
            </p>
          </>
        )}
        <ul className={`${compact ? "" : "mt-4"} space-y-3`}>
          {actions.map((a) => <Option key={a.id} a={a} recommended={a.rank === 1} decision={decision} compact={compact} />)}
        </ul>
      </div>
    </section>
  );
}
