// Printable audit report → the browser's "Save as PDF". Built only from data the Audit page already loaded
// (cases, their recorded audit events, the order systems' action log). No backend call, nothing invented.
import { dateTimeSec } from "./format";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS = { open: "Detected", investigating: "Being investigated", awaiting_approval: "Waiting for a decision",
  auto_resolved: "Fixed automatically", resolved: "Fixed after approval", rejected: "Closed without action", failed: "Agent stopped" };
const DOMAIN = { inventory: "Stock", supplier: "Supplier", payment: "Payment", pricing: "Pricing", deadline: "Delivery deadline" };
const TYPE = { investigation: "Investigation", policy: "Policy decision", approval: "Approval / rejection", action: "Action",
  escalation: "Escalation", failure: "Failure / fallback", notification: "Notification", system: "System action" };
const rupees = (n) => `${Number(n) < 0 ? "−" : ""}₹${Math.abs(Number(n || 0)).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

function spent(events) {
  let total = 0;
  for (const e of events) {
    if (e.raw?.kind === "execution") total += Number(e.raw.actual_cost || 0);
    if (e.raw?.kind === "rollback" && e.raw.ok) total -= Number(e.raw.cost_reversed || 0);
  }
  return Math.max(0, total);
}

export function buildAuditReport({ exceptions, rows, filterLabel, exportedBy, now = new Date() }) {
  const caseRows = rows.filter((r) => r.exception_id);
  const sysRows = rows.filter((r) => !r.exception_id).sort((a, b) => a.ts.localeCompare(b.ts));
  const byCase = new Map();
  for (const r of caseRows) {
    if (!byCase.has(r.exception_id)) byCase.set(r.exception_id, []);
    byCase.get(r.exception_id).push(r);
  }
  const cases = exceptions.filter((e) => byCase.has(e.id)).sort((a, b) => a.id - b.id);
  const count = (s) => cases.filter((e) => e.status === s).length;
  const allEvents = [...byCase.values()].flat();
  const approvals = allEvents.filter((e) => e.type === "approval");
  const stamp = now.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

  const caseHtml = cases.map((e) => {
    const ev = byCase.get(e.id).slice().sort((a, b) => a.ts.localeCompare(b.ts) || (a.step - b.step));
    const decided = ev.filter((x) => x.type === "approval").map((x) => `${esc(x.actor)} (${esc(x.status)})`).join(", ");
    return `
    <section class="case">
      <div class="case-head">
        <h2>${esc(e.order_id)} <span class="muted">· ${esc(DOMAIN[e.domain] || e.domain)} problem · ${esc(e.severity)} severity</span></h2>
        <span class="pill s-${esc(e.status)}">${esc(STATUS[e.status] || e.status)}</span>
      </div>
      <table class="facts"><tbody>
        <tr><th>Customer</th><td>${esc(e.customer_name || "—")}${e.customer_tier ? ` (${esc(e.customer_tier)})` : ""}${e.order_value ? ` · order value ${rupees(e.order_value)}` : ""}</td></tr>
        <tr><th>What went wrong</th><td>${esc(e.symptom || "—")}</td></tr>
        <tr><th>Root cause</th><td>${esc(e.root_cause || "—")}${e.confidence != null ? ` <span class="muted">(confidence ${Math.round(e.confidence * 100)}%)</span>` : ""}</td></tr>
        <tr><th>Detected</th><td>${esc(dateTimeSec(e.created_at))}${e.resolved_at ? ` · closed ${esc(dateTimeSec(e.resolved_at))}` : ""}</td></tr>
        <tr><th>Decided by</th><td>${decided || (e.status === "auto_resolved" ? "Automation, within policy limits" : "—")}</td></tr>
        <tr><th>Money spent</th><td>${rupees(spent(ev))}</td></tr>
      </tbody></table>
      <table class="events"><thead><tr><th class="t">Time</th><th class="k">Event</th><th class="a">By</th><th class="st">Status</th><th>Details</th></tr></thead><tbody>
        ${ev.map((x) => `<tr><td class="t">${esc(dateTimeSec(x.ts))}</td><td class="k">${esc(TYPE[x.type] || x.type)}</td><td class="a">${esc(x.actor)}</td><td class="st">${esc(x.status)}</td><td>${esc(x.text)}</td></tr>`).join("")}
      </tbody></table>
    </section>`;
  }).join("");

  const sysHtml = sysRows.length ? `
    <section class="case system">
      <h2>Order systems' action log <span class="muted">· every change made to an order</span></h2>
      <table class="events"><thead><tr><th class="t">Time</th><th class="k">Order</th><th class="st">Status</th><th>What changed</th><th class="a">Cost</th></tr></thead><tbody>
        ${sysRows.map((x) => `<tr><td class="t">${esc(dateTimeSec(x.ts))}</td><td class="k">${esc(x.order_id || "—")}</td><td class="st">${esc(x.status)}</td><td>${esc(x.text)}</td><td class="a">${x.raw?.cost != null ? rupees(x.raw.cost) : ""}</td></tr>`).join("")}
      </tbody></table>
    </section>` : "";

  const title = `OrderGuard-audit-${now.toISOString().slice(0, 10)}`;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>
  @page { size: A4; margin: 14mm 12mm; }
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", Arial, sans-serif; color: #111827; font-size: 10.5pt; margin: 0; }
  .cover { border-bottom: 2px solid #0f766e; padding-bottom: 10px; margin-bottom: 14px; }
  .brand { color: #0f766e; font-weight: 700; font-size: 11pt; letter-spacing: .04em; }
  h1 { font-size: 20pt; margin: 2px 0 4px; }
  .meta { color: #4b5563; font-size: 9.5pt; }
  .stats { display: flex; gap: 8px; margin: 12px 0 4px; flex-wrap: wrap; }
  .stat { border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 10px; min-width: 105px; }
  .stat b { display: block; font-size: 14pt; }
  .stat span { color: #4b5563; font-size: 8.5pt; }
  .case { margin: 0 0 16px; page-break-inside: auto; }
  .case-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; border-bottom: 1px solid #d1d5db; padding-bottom: 3px; page-break-after: avoid; }
  h2 { font-size: 12.5pt; margin: 0; page-break-after: avoid; }
  .muted { color: #6b7280; font-weight: 400; }
  .pill { font-size: 8.5pt; border-radius: 999px; padding: 1px 8px; border: 1px solid #9ca3af; white-space: nowrap; }
  .s-auto_resolved, .s-resolved { border-color: #15803d; color: #15803d; }
  .s-awaiting_approval { border-color: #b45309; color: #b45309; }
  .s-failed { border-color: #b91c1c; color: #b91c1c; }
  table { width: 100%; border-collapse: collapse; }
  .facts { margin: 6px 0 8px; page-break-inside: avoid; }
  .facts th { text-align: left; width: 120px; color: #4b5563; font-weight: 600; vertical-align: top; padding: 2px 8px 2px 0; }
  .facts td { padding: 2px 0; vertical-align: top; }
  .events { font-size: 9pt; }
  .events thead { display: table-header-group; }
  .events th { text-align: left; background: #f3f4f6; color: #374151; font-weight: 600; padding: 4px 6px; border-bottom: 1px solid #d1d5db; }
  .events td { padding: 4px 6px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
  .events tr { page-break-inside: avoid; }
  .t { width: 118px; white-space: nowrap; color: #4b5563; } .k { width: 108px; } .a { width: 110px; } .st { width: 86px; }
  .system { page-break-before: auto; }
  .foot { margin-top: 18px; color: #6b7280; font-size: 8.5pt; border-top: 1px solid #e5e7eb; padding-top: 6px; }
</style></head><body>
  <div class="cover">
    <div class="brand">ORDERGUARD AI</div>
    <h1>Audit report</h1>
    <div class="meta">Exported ${esc(stamp)} by ${esc(exportedBy || "—")} · ${esc(filterLabel)}</div>
    <div class="stats">
      <div class="stat"><b>${cases.length}</b><span>cases</span></div>
      <div class="stat"><b>${count("auto_resolved")}</b><span>fixed automatically</span></div>
      <div class="stat"><b>${count("resolved")}</b><span>fixed after approval</span></div>
      <div class="stat"><b>${count("rejected")}</b><span>closed without action</span></div>
      <div class="stat"><b>${count("awaiting_approval")}</b><span>still waiting</span></div>
      <div class="stat"><b>${approvals.length}</b><span>human decisions</span></div>
      <div class="stat"><b>${rupees(spent(allEvents))}</b><span>spent on fixes</span></div>
    </div>
  </div>
  ${caseHtml || `<p class="muted">No case events match this filter.</p>`}
  ${sysHtml}
  <div class="foot">Generated from OrderGuard AI's recorded audit trail and the order systems' action log. Read-only record; nothing in this report was edited.</div>
</body></html>`;
}

// Opens the browser's print dialog for the report (choose "Save as PDF"). Uses a hidden frame, so no pop-up blocker.
export function printAuditReport(opts) {
  const html = buildAuditReport(opts);
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.appendChild(frame);
  const doc = frame.contentWindow.document;
  doc.open(); doc.write(html); doc.close();
  const prevTitle = document.title;
  const go = () => {
    document.title = doc.title;                    // Chrome uses the page title as the PDF file name
    frame.contentWindow.focus();
    frame.contentWindow.print();
    setTimeout(() => { document.title = prevTitle; frame.remove(); }, 1500);
  };
  if (doc.readyState === "complete") setTimeout(go, 50); else frame.onload = go;
  return html;
}
