// Turns recorded trail steps into the views the screens need. Everything is derived from server data;
// when the data doesn't say something, these return null and the UI says "not recorded".

import { TOOL, humanize } from "./format";

export const ACTION_TYPES = new Set(["reallocate_stock", "switch_supplier", "retry_payment", "hold_order", "release_order",
  "apply_price_correction", "expedite_shipping", "notify_customer", "undo"]);

/** The latest successful undo of an automatic fix, if it is newer than the last fix that ran (FR-15). */
export function rollback(trail) {
  let last = null;
  for (const s of trail) {
    if (s.type !== "decision") continue;
    if (s.content.kind === "rollback" && s.content.ok) last = s;
    else if (s.content.kind === "execution") last = null;
  }
  return last;
}

/** Latest successful result of a read-only check, e.g. lastResult(trail, "get_sla"). */
export function lastResult(trail, tool) {
  for (let i = trail.length - 1; i >= 0; i--) {
    const s = trail[i];
    if (s.type === "tool_result" && s.content.tool === tool && s.content.ok && s.content.data) return s.content.data;
  }
  return null;
}

export const decisions = (trail, kind) =>
  trail.filter((s) => s.type === "decision" && (!kind || s.content.kind === kind));

/** Plain-language facts for the investigation header. Each fact cites the check it came from. */
export function keyFacts(exc, trail) {
  const facts = [];
  const o = exc.order;
  const sla = lastResult(trail, "get_sla");
  if (sla) {
    const promised = new Date(sla.promised_date);
    const eta = new Date(sla.current_eta);
    const late = lateBy(eta - promised);
    facts.push({
      label: "Delivery",
      value: sla.at_risk ? `Expected ${late ? `${late} after` : "after"} the promised date` : "Expected on or before the promised date",
      detail: `Promised ${fmt(promised)}, expected ${fmt(eta)} when checked`,
      tone: sla.at_risk ? "crit" : "good",
      source: "get_sla",
    });
  } else if (o?.promised_date) {
    facts.push({ label: "Delivery", value: `Promised ${fmt(new Date(o.promised_date))}`, detail: "No delivery check recorded",
      tone: "muted", source: null });
  }
  const sup = lastResult(trail, "get_supplier_status");
  const po = sup?.purchase_orders?.[0];
  if (po) {
    facts.push({
      label: "Supplier",
      value: `${po.supplier?.name || po.supplier_id}: ${po.status === "delayed" ? "delayed" : po.status}`,
      detail: po.delay_reason || `${po.id} arriving in ${po.days_until_arrival} days`,
      tone: po.is_delayed ? "crit" : "good",
      source: "get_supplier_status",
    });
  } else if (o?.purchase_orders?.length) {
    const p = o.purchase_orders[0];
    facts.push({ label: "Supplier", value: `${p.supplier_id}, ${p.id} ${p.status}`, detail: "From the order record",
      tone: p.status === "delayed" ? "crit" : "muted", source: "get_order" });
  }
  const inv = lastResult(trail, "check_inventory");
  if (inv) {
    const bad = inv.locations.filter((l) => l.scan_mismatch);
    facts.push({
      label: "Stock",
      value: bad.length
        ? `${bad.map((l) => l.warehouse_id).join(", ")} count doesn't match the shelf`
        : `${inv.total_verified_available} verified units available`,
      detail: inv.locations.map((l) => `${l.warehouse_id}: system ${l.on_hand}, shelf ${l.last_scan_qty}`).join("; "),
      tone: bad.length ? "crit" : "good",
      source: "check_inventory",
    });
  }
  const pay = lastResult(trail, "get_payment_status");
  if (pay) {
    const r = pay.risk_signals || {};
    const fraud = r.max_fraud_score ?? 0;
    facts.push({
      label: "Payment",
      value: r.card_expired ? "Card expired" : fraud >= 0.5 ? "Fraud signals present" : r.decline_count ? "Declined" : "No payment issue",
      detail: [`${r.decline_count || 0} declined attempt(s)`, `fraud score ${fraud.toFixed(2)}`,
        r.ip_billing_country_mismatch ? "IP country differs from billing country" : null].filter(Boolean).join(", "),
      tone: r.card_expired || r.decline_count ? (fraud >= 0.5 ? "crit" : "warn") : "good",
      source: "get_payment_status",
    });
  }
  const price = lastResult(trail, "get_pricing_rules");
  if (price) {
    const charged = price.charged_total ?? price.price_as_applied;
    const broken = price.violations?.length || 0;
    facts.push({
      label: "Price",
      value: broken ? `Charged ₹${n(charged)}, rules allow ₹${n(price.price_by_rules)}` : "Price follows the rules",
      detail: broken ? price.violations.map((v) => v.message || v.rule.replaceAll("_", " ")).join("; ")
        : `Charged ₹${n(charged)}`,
      tone: broken ? "crit" : "good",
      source: "get_pricing_rules",
    });
  }
  return facts;
}

/** "18 hours", "1.5 days", "6 days": rounded to what the gap actually is, never up to a whole day. */
function lateBy(ms) {
  const h = ms / 3600000;
  if (h <= 0) return "";
  if (h < 24) return `${Math.max(1, Math.round(h))} hour${Math.round(h) === 1 ? "" : "s"}`;
  const d = Math.round((h / 24) * 2) / 2;
  return `${d} day${d === 1 ? "" : "s"}`;
}

const fmt = (d) => d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
const n = (x) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(x);

/** Actions that actually ran (successful action calls in the trail). */
export function executedActions(trail) {
  const out = [];
  for (let i = 0; i < trail.length; i++) {
    const s = trail[i];
    if (s.type === "tool_result" && ACTION_TYPES.has(s.content.tool)) {
      out.push({ tool: s.content.tool, label: TOOL[s.content.tool] || s.content.tool, ok: s.content.ok,
        message: s.content.summary || s.content.error, cost: s.content.data?.cost, ts: s.ts });
    }
  }
  return out;
}

// ───────── workflow stages (the automation pipeline), derived per case ─────────

export const STAGES = [
  { key: "detect", label: "Order detection", node: "OEA 1 · Run detector rules" },
  { key: "investigate", label: "AI investigation", node: "Check systems → Ask Claude / rule playbook" },
  { key: "policy", label: "Policy evaluation", node: "Apply approval policy" },
  { key: "approval", label: "Human approval", node: "Wait for a person" },
  { key: "action", label: "Action or escalation", node: "Fix it automatically / Run the approved fix" },
  { key: "notify", label: "Customer notification", node: "notify_customer step, when an option includes it" },
  { key: "audit", label: "Audit record", node: "Resolution written to the case record" },
];

/** state: done | running | waiting | failed | skipped | pending | unknown */
export function stageStates(exc, trail) {
  const kinds = new Set(decisions(trail).map((s) => s.content.kind));
  const toolCalls = trail.filter((s) => s.type === "tool_call" && !ACTION_TYPES.has(s.content.tool));
  const executed = executedActions(trail);
  const st = exc.status;
  const closed = ["auto_resolved", "resolved", "rejected"].includes(st);
  const human = decisions(trail, "human");
  const undone = rollback(trail);
  const r = {};
  r.detect = "done";
  r.investigate = kinds.has("diagnosis") ? "done" : st === "failed" ? "failed" : st === "investigating" ? "running"
    : toolCalls.length ? "running" : "pending";
  r.policy = kinds.has("policy") ? "done" : st === "failed" ? "failed" : "pending";
  if (kinds.has("escalation") || undone) {
    r.approval = human.length ? "done" : st === "awaiting_approval" ? "waiting" : st === "failed" ? "failed" : "pending";
  } else {
    r.approval = kinds.has("policy") ? "skipped" : "pending";
  }
  const fixes = executed.filter((a) => a.tool !== "hold_order" || kinds.has("execution"));
  const anyFail = executed.some((a) => !a.ok);
  r.action = undone ? (st === "rejected" ? "skipped" : "waiting") : kinds.has("execution") ? "done" : st === "rejected" ? "skipped" : anyFail ? "failed"
    : st === "awaiting_approval" ? "waiting" : "pending";
  if (st === "awaiting_approval" && kinds.has("containment")) r.action = "waiting";
  const notified = fixes.some((a) => a.tool === "notify_customer" && a.ok);
  if (undone && st === "awaiting_approval") r.approval = "waiting";
  r.notify = notified ? "done" : closed ? "unknown" : "pending";
  r.audit = closed ? "done" : "pending";
  return r;
}

export const STAGE_STATE = {
  done: { label: "Completed", tone: "good", icon: "✓" },
  running: { label: "Running", tone: "live", icon: "◌" },
  waiting: { label: "Awaiting approval", tone: "warn", icon: "‖" },
  failed: { label: "Failed", tone: "crit", icon: "✕" },
  skipped: { label: "Not needed", tone: "muted", icon: "–" },
  pending: { label: "Pending", tone: "muted", icon: "·" },
  unknown: { label: "Not recorded", tone: "muted", icon: "?" },
};

// ───────── audit events from a case trail ─────────

export const VERDICT = { approved: "Approved", rejected: "Rejected", undo: "Asked to undo" };

export function auditEvents(exc, trail) {
  const ev = [];
  for (const s of trail) {
    const c = s.content;
    const base = { ts: s.ts, exception_id: exc.id, order_id: exc.order_id, step: s.step_no, raw: c };
    if (s.type === "decision") {
      const k = c.kind;
      if (k === "diagnosis") ev.push({ ...base, type: "investigation", actor: c.source === "llm" ? "Claude" : "Rule playbook",
        status: "completed", text: `Root cause: ${c.root_cause}` });
      else if (k === "policy") ev.push({ ...base, type: "policy", actor: c.engine === "n8n" ? "n8n policy node" : "Policy engine",
        status: c.options?.[0]?.auto ? "auto-approved" : "needs approval",
        text: (c.options || []).map((o) => `${o.title}: ${o.auto ? "can run automatically" : `needs approval (${humanize(o.reasons.join("; "))})`}`).join("; ") });
      else if (k === "escalation") ev.push({ ...base, type: "escalation", actor: "Automation", status: "escalated",
        text: `Sent to a person: ${humanize(c.reason)}` });
      else if (k === "human") ev.push({ ...base, type: "approval", actor: c.by, status: c.verdict === "undo" ? "undo requested" : c.verdict,
        text: `${VERDICT[c.verdict] || c.verdict} "${c.title}"${c.feedback ? `: ${c.feedback}` : ""}${c.modified ? " (with edits)" : ""}` });
      else if (k === "rollback") ev.push({ ...base, type: "action", actor: c.by || "Automation", status: c.ok ? "undone" : "failed",
        text: c.ok ? `Automatic fix undone: ${(c.steps || []).join("; ")}${c.cost_reversed ? `, ₹${c.cost_reversed} reversed` : ""}`
          : `Undo stopped: ${c.error}` });
      else if (k === "execution") ev.push({ ...base, type: "action", actor: "Automation", status: "completed",
        text: `Fix completed, cost ₹${c.actual_cost}` });
      else if (k === "containment") ev.push({ ...base, type: "action", actor: "Automation", status: c.ok ? "completed" : "skipped",
        text: c.ok ? "Order held as a precaution" : c.note });
      else if (k === "fallback") ev.push({ ...base, type: "failure", actor: "Automation", status: "fallback", text: c.reason });
      else if (k === "cost_check") ev.push({ ...base, type: "policy", actor: "Cost check", status: "corrected",
        text: `"${c.option}" cost corrected from ₹${c.claimed} to ₹${c.verified}` });
    } else if (s.type === "tool_result" && ACTION_TYPES.has(c.tool)) {
      ev.push({ ...base, type: c.tool === "notify_customer" ? "notification" : "action", actor: "Automation",
        status: c.ok ? "completed" : "failed", text: `${TOOL[c.tool] || c.tool}: ${c.summary || c.error || ""}` });
    }
  }
  return ev;
}
