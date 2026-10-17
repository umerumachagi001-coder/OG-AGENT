import { useMemo, useState } from "react";
import { rupees } from "../format";

// Every dot is a real order from the mock order system, placed on the ring for its fulfilment stage and sized by
// order value. Orders with a live exception are coloured by what the exception needs, and labelled with their ID
// so the state never depends on colour alone.

export const RINGS = [
  { key: "pay", label: "Placed and paid", statuses: ["placed", "payment_pending", "paid", "payment_failed"] },
  { key: "wh", label: "In the warehouse", statuses: ["allocated", "picking", "pick_failed", "packed", "awaiting_stock", "on_hold", "pricing_review"] },
  { key: "road", label: "On the road", statuses: ["shipped"] },
  { key: "done", label: "Delivered", statuses: ["delivered"] },
];

export const ORDER_STATE = {
  problem: { label: "Problem being handled", color: "var(--color-crit)" },
  decision: { label: "Waiting for your decision", color: "var(--color-warn)" },
  fixed: { label: "Fixed today", color: "var(--color-good)" },
  normal: { label: "Moving normally", color: "var(--color-neutral)" },
};

export function orderState(exc) {
  if (!exc) return "normal";
  if (exc.status === "awaiting_approval") return "decision";
  if (["auto_resolved", "resolved", "rejected"].includes(exc.status)) return "fixed";
  return "problem";
}

// Stable pseudo-random position per order ID, so demo orders don't all bunch at 12 o'clock.
const hash = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };

const SIZE = 560;
const C = SIZE / 2;
const RADII = [78, 138, 196, 252];

export default function OrderOrbit({ orders, exceptions, onOpen }) {
  const [hover, setHover] = useState(null);

  const latest = useMemo(() => {
    const m = {};
    for (const e of exceptions) if (!m[e.order_id] || e.id > m[e.order_id].id) m[e.order_id] = e;
    return m;
  }, [exceptions]);

  const dots = useMemo(() => {
    if (!orders.length) return [];
    const maxV = Math.max(...orders.map((o) => o.total));
    const out = [];
    RINGS.forEach((ring, ri) => {
      const list = orders.filter((o) => ring.statuses.includes(o.status)).sort((a, b) => hash(a.id) - hash(b.id));
      const n = list.length;
      list.forEach((o, i) => {
        // start at the top, leave a small gap at 12 o'clock for the ring label
        const a = -Math.PI / 2 + 0.34 + ((i + 0.5) / Math.max(n, 1)) * (Math.PI * 2 - 0.68);
        const r = RADII[ri];
        const exc = latest[o.id];
        out.push({
          o, exc, state: orderState(exc),
          x: C + r * Math.cos(a), y: C + r * Math.sin(a),
          rad: 4 + 9 * Math.sqrt(o.total / maxV),
          a,
        });
      });
    });
    return out;
  }, [orders, latest]);

  const counts = RINGS.map((r) => orders.filter((o) => r.statuses.includes(o.status)).length);
  const flagged = dots.filter((d) => d.state !== "normal");
  const openProblems = dots.filter((d) => d.state === "problem" || d.state === "decision").length;

  if (!orders.length) return null;
  return (
    <figure className="relative">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="orbit-in mx-auto block w-full max-w-[560px]" role="img"
        aria-label={`${orders.length} orders by fulfilment stage. ${openProblems} with an open problem.`}>
        <defs>
          <radialGradient id="core" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#3ccfd3" stopOpacity="0.16" />
            <stop offset="100%" stopColor="#3ccfd3" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={C} cy={C} r={RADII[0] - 30} fill="url(#core)" />
        {RADII.map((r, i) => (
          <g key={i}>
            <circle cx={C} cy={C} r={r} fill="none" stroke="var(--color-line)" strokeWidth="1" />
            <text x={C} y={C - r - 7} textAnchor="middle" className="fill-[var(--color-dim)] text-[11px]">
              {RINGS[i].label} ({counts[i]})
            </text>
          </g>
        ))}
        <text x={C} y={C - 4} textAnchor="middle" className="fill-[var(--color-ink)] font-cond text-[34px] font-bold">
          {openProblems}
        </text>
        <text x={C} y={C + 16} textAnchor="middle" className="fill-[var(--color-muted)] text-[12px]">
          {openProblems === 1 ? "order needs attention" : "orders need attention"}
        </text>

        {dots.map((d) => {
          const st = ORDER_STATE[d.state];
          const clickable = !!d.exc;
          return (
            <g key={d.o.id}
              onMouseEnter={() => setHover(d)} onMouseLeave={() => setHover(null)}
              onClick={() => clickable && onOpen(d.exc.id)}
              onKeyDown={(e) => clickable && (e.key === "Enter" || e.key === " ") && onOpen(d.exc.id)}
              tabIndex={clickable ? 0 : -1} role={clickable ? "button" : undefined}
              aria-label={clickable ? `${d.o.id}: ${st.label}. Open investigation` : undefined}
              className={clickable ? "cursor-pointer outline-none" : ""}>
              <circle cx={d.x} cy={d.y} r={d.rad + 6} fill="transparent" />
              {d.state !== "normal" && (
                <circle cx={d.x} cy={d.y} r={d.rad + 4} fill="none" stroke={st.color} strokeOpacity="0.45" strokeWidth="2" />
              )}
              <circle cx={d.x} cy={d.y} r={d.rad} fill={st.color} fillOpacity={d.state === "normal" ? 0.55 : 1}
                stroke="var(--color-deck)" strokeWidth="2" />
            </g>
          );
        })}
        {flagged.map((d) => {
          const out = Math.cos(d.a) >= 0;
          return (
            <text key={d.o.id + "l"} x={d.x + (out ? d.rad + 9 : -(d.rad + 9))} y={d.y + 4} textAnchor={out ? "start" : "end"}
              className="pointer-events-none fill-[var(--color-ink)] font-cond text-[13px] font-semibold">
              {d.o.id}
            </text>
          );
        })}
      </svg>

      {hover && (
        <div className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 rounded-lg bg-raised px-3 py-2 text-[13px] shadow-lg ring-1 ring-line-strong">
          <p className="font-cond text-[15px] font-semibold">{hover.o.id}</p>
          <p className="text-muted">{hover.o.customer_name} ({hover.o.customer_tier}), {rupees(hover.o.total)}</p>
          <p className="text-muted">Order status: {hover.o.status.replaceAll("_", " ")}</p>
          <p>{ORDER_STATE[hover.state].label}{hover.exc ? ". Click to open" : ""}</p>
        </div>
      )}

      <figcaption className="mt-3 flex flex-wrap justify-center gap-x-5 gap-y-1.5 text-[12.5px] text-muted">
        {Object.entries(ORDER_STATE).map(([k, s]) => (
          <span key={k} className="flex items-center gap-1.5">
            <svg width="14" height="14" aria-hidden><circle cx="7" cy="7" r="5" fill={s.color} fillOpacity={k === "normal" ? 0.55 : 1} /></svg>
            {s.label}
          </span>
        ))}
        <span className="text-dim">Dot size shows order value</span>
      </figcaption>
    </figure>
  );
}
