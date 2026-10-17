import { ACTION_TYPES, VERDICT } from "../derive";
import { TOOL, humanize, rupees, stepDetail } from "../format";
import { RawJson } from "./ui";

// The investigation timeline: every recorded step, labelled by what kind of statement it is, so a reader can tell
// an observed fact from an inference, a recommendation, or an action that actually ran.

function group(steps) {
  const out = [];
  for (const s of steps) {
    const c = s.content;
    if (s.type === "tool_result") {
      const prev = out[out.length - 1];
      if (prev?.type === "tool" && prev.call.tool === c.tool && !prev.result) { prev.result = c; continue; }
    }
    if (s.type === "tool_call") out.push({ type: "tool", key: s.step_no, call: c, ts: s.ts });
    else out.push({ type: s.type, key: s.step_no, c, ts: s.ts });
  }
  return out;
}

const KIND = {
  fact: { label: "Observed", cls: "text-data" },
  think: { label: "Reasoning", cls: "text-muted" },
  assess: { label: "Assessment", cls: "text-signal" },
  policy: { label: "Policy", cls: "text-signal" },
  person: { label: "Person", cls: "text-warn" },
  action: { label: "Action ran", cls: "text-good" },
  failed: { label: "Failed", cls: "text-crit" },
  system: { label: "System", cls: "text-muted" },
};

const DOT = { good: "bg-good border-good", crit: "bg-crit border-crit", warn: "bg-warn border-warn", signal: "bg-signal border-signal",
  hollow: "bg-deck border-line-strong", data: "bg-deck border-data" };

function Row({ kind, dot = "hollow", ts, title, children }) {
  const k = KIND[kind];
  return (
    <li className="step-in relative pb-5 pl-6">
      <span aria-hidden className={`absolute left-[-6px] top-[6px] size-[11px] rounded-full border-2 ${DOT[dot]}`} />
      <p className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
        <span className={`font-semibold ${k.cls}`}>{k.label}</span>
        <span className="text-dim">{ts ? new Date(ts).toLocaleTimeString("en-IN") : ""}</span>
      </p>
      <div className="mt-0.5">{title}</div>
      {children}
    </li>
  );
}

function ToolRow({ g }) {
  const r = g.result;
  const isAction = ACTION_TYPES.has(g.call.tool);
  const detail = isAction ? stepDetail({ type: g.call.tool, params: g.call.input }) : g.call.input?.sku || g.call.input?.po_id || "";
  const failed = r && !r.ok;
  return (
    <Row kind={failed ? "failed" : isAction ? "action" : "fact"} dot={failed ? "crit" : isAction ? "good" : "data"} ts={g.ts}
      title={<p><span className="font-medium">{TOOL[g.call.tool] || g.call.tool}</span>{detail && <span className="text-muted"> ({detail})</span>}</p>}>
      {!r && <p className="text-[14px] text-muted">Waiting for the system to answer…</p>}
      {r && <p className={`text-[14px] leading-snug ${r.ok ? "text-muted" : "text-crit"}`}>{r.ok ? r.summary || "Done." : r.error}</p>}
      {r?.data && <RawJson data={r.data} />}
    </Row>
  );
}

function Decision({ g }) {
  const c = g.c;
  switch (c.kind) {
    case "diagnosis":
      return (
        <Row kind="assess" dot="signal" ts={g.ts}
          title={<p className="font-medium">Root cause identified by {c.source === "llm" ? "Claude" : "the rule playbook"}, {Math.round(c.confidence * 100)}% confidence</p>}>
          <p className="text-[14px] text-ink/90">{c.root_cause}</p>
        </Row>
      );
    case "policy":
      return (
        <Row kind="policy" dot="signal" ts={g.ts} title={<p className="font-medium">{c.engine === "n8n" ? "n8n applied the approval policy" : "Approval policy applied"}</p>}>
          <ul className="space-y-0.5 text-[14px]">
            {c.options.map((o) => (
              <li key={o.rank}>{o.title}: {o.auto ? <span className="text-good">allowed to run automatically</span>
                : <span className="text-warn">needs a person ({humanize(o.reasons.join("; "))})</span>}</li>
            ))}
          </ul>
        </Row>
      );
    case "execution":
      return <Row kind="action" dot="good" ts={g.ts} title={<p className="font-medium text-good">Fix completed{c.actual_cost ? `, cost ${rupees(c.actual_cost)}` : ", no extra cost"}</p>} />;
    case "escalation":
      return <Row kind="person" dot="warn" ts={g.ts} title={<p className="font-medium text-warn">Sent to a person</p>}>
        <p className="text-[14px] text-muted">{humanize(c.reason)}</p></Row>;
    case "containment":
      return <Row kind={c.ok ? "action" : "system"} dot={c.ok ? "good" : "hollow"} ts={g.ts}
        title={<p className="font-medium">{c.ok ? "Order protected straight away" : "Protective step skipped"}</p>}>
        <p className="text-[14px] text-muted">{c.ok ? c.steps?.join(". ") : c.note}</p></Row>;
    case "cost_check":
      return <Row kind="policy" dot="signal" ts={g.ts} title={<p className="font-medium">Cost estimate corrected</p>}>
        <p className="text-[14px] text-muted">The proposal said {rupees(c.claimed)}; the live systems say {rupees(c.verified)}. The policy used the system figure.</p></Row>;
    case "fallback":
      return <Row kind="system" dot="warn" ts={g.ts} title={<p className="font-medium">Switched to the rule playbook</p>}>
        <p className="text-[14px] text-muted">{c.reason}</p></Row>;
    case "human":
      return <Row kind="person" dot={c.verdict === "approved" ? "good" : c.verdict === "undo" ? "warn" : "crit"} ts={g.ts}
        title={<p className="font-medium">{VERDICT[c.verdict] || c.verdict} by {c.by}{c.modified ? ", with edits" : ""}</p>}>
        <p className="text-[14px] text-muted">{c.feedback ? `${c.title}: ${c.feedback}` : c.title}</p></Row>;
    case "rollback":
      return <Row kind={c.ok ? "action" : "failed"} dot={c.ok ? "warn" : "crit"} ts={g.ts}
        title={<p className={`font-medium ${c.ok ? "text-warn" : "text-crit"}`}>{c.ok ? "Automatic fix undone" : "Undo stopped"}</p>}>
        <p className="text-[14px] text-muted">{c.ok ? `${c.note}${c.cost_reversed ? ` ${rupees(c.cost_reversed)} reversed.` : ""}` : c.error}</p></Row>;
    default:
      return <Row kind="system" ts={g.ts} title={<p>{c.kind}</p>}><RawJson data={c} /></Row>;
  }
}

export default function Trail({ steps, live }) {
  const groups = group(steps);
  return (
    <div>
      {!groups.length && <p className="text-[14px] text-muted">Starting the investigation…</p>}
      <ol className="relative ml-1.5 border-l border-line">
        {groups.map((g) =>
          g.type === "tool" ? <ToolRow key={g.key} g={g} />
            : g.type === "thought" ? (
              <Row key={g.key} kind="think" ts={g.ts} title={<p className="max-w-[72ch] italic text-ink/80">{g.c.text}</p>} />
            ) : <Decision key={g.key} g={g} />
        )}
        {live && (
          <li className="relative pl-6">
            <span className="pulse-dot absolute left-[-6px] top-[6px] size-[11px] rounded-full bg-signal" aria-hidden />
            <p className="text-[14px] text-muted">Working…</p>
          </li>
        )}
      </ol>
    </div>
  );
}
