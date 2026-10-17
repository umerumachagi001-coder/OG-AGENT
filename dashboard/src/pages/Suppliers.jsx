import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Chip, Empty, ErrorNote, Loading, Panel } from "../components/ui";
import { rupees, shortDate } from "../format";
import { useStore } from "../store";

// Supplier risk from real supplier records. The risk measure is the supplier's recorded on-time reliability;
// bands are fixed thresholds stated on the page. Purchase orders come from the supplier system for orders that
// are waiting on stock.

const band = (r) => (r >= 0.95 ? { label: "Low risk", tone: "good" } : r >= 0.9 ? { label: "Moderate risk", tone: "warn" } : { label: "Elevated risk", tone: "crit" });

export default function Suppliers() {
  const { orders } = useStore();
  const [suppliers, setSuppliers] = useState(null);
  const [pos, setPos] = useState([]);
  const [error, setError] = useState(null);
  const [sel, setSel] = useState(null);
  const [term, setTerm] = useState("");
  const [n, setN] = useState(0);
  const waiting = orders.filter((o) => o.status === "awaiting_stock" || o.status === "on_hold").map((o) => o.id).join(",");

  useEffect(() => {
    let alive = true;
    api.suppliers().then((s) => { if (alive) { setSuppliers(s); setError(null); } }).catch((e) => alive && setError(e));
    Promise.all(waiting.split(",").filter(Boolean).map((id) => api.supplierStatus({ order_id: id }).then((r) => r.purchase_orders).catch(() => [])))
      .then((all) => alive && setPos(all.flat()));
    return () => { alive = false; };
  }, [waiting, n]);

  const rows = useMemo(() => (suppliers || [])
    .filter((s) => !term || `${s.name} ${s.city} ${s.id}`.toLowerCase().includes(term.toLowerCase()))
    .map((s) => ({ ...s, pos: pos.filter((p) => p.supplier_id === s.id), alt: pos.flatMap((p) => p.alternates).filter((a) => a.supplier_id === s.id) }))
    .sort((a, b) => a.reliability - b.reliability), [suppliers, pos, term]);

  const picked = rows.find((r) => r.id === sel);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-cond text-[30px] font-bold leading-tight">Supplier risk</h1>
        <p className="max-w-[80ch] text-[14.5px] text-muted">
          Risk is each supplier's recorded on-time delivery rate: 95% or better is low risk, 90–95% moderate, below 90% elevated.
          {suppliers && ` ${suppliers.length} suppliers on record.`}
        </p>
      </header>
      {error && <ErrorNote error={{ message: `Supplier system unavailable: ${error.message}` }} onRetry={() => setN((x) => x + 1)} />}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="On-time delivery rate" action={<span className="text-[12px] text-dim">Scale starts at 80%</span>}>
          {!suppliers ? (!error && <Loading />) : <ReliabilityPlot rows={rows} sel={sel} onPick={setSel} />}
        </Panel>

        <Panel title={picked ? picked.name : "Supplier details"}>
          {!picked ? <p className="text-[14px] text-muted">Select a supplier in the chart or the table.</p> : (
            <div className="space-y-3 text-[14px]">
              <p className="flex flex-wrap items-center gap-2">
                <Chip tone={band(picked.reliability).tone}>{band(picked.reliability).label}</Chip>
                <span>{Math.round(picked.reliability * 100)}% on time</span>
                <span className="text-muted">{picked.city}, {picked.id}</span>
              </p>
              <p className="text-muted">Orders desk: {picked.contact_email}</p>
              <div>
                <p className="font-medium">Open purchase orders for waiting customer orders</p>
                {picked.pos.length ? (
                  <ul className="mt-1 space-y-1.5">
                    {picked.pos.map((p) => (
                      <li key={p.id} className={`rounded-lg bg-raised px-3 py-2 ring-1 ${p.is_delayed ? "ring-crit/50" : "ring-line"}`}>
                        <span className="font-semibold">{p.id}</span> for {p.for_order_id}: {p.qty} × {p.sku}
                        {p.is_delayed ? <span className="text-crit">, delayed: {p.delay_reason}</span> : `, ${p.status}`}
                        <span className="block text-[12.5px] text-dim">Expected {shortDate(p.expected_arrival)} ({p.days_until_arrival} days)</span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-muted">None right now.</p>}
              </div>
              {picked.alt.length > 0 && (
                <div>
                  <p className="font-medium">Can replace a delayed order</p>
                  <ul className="mt-1 space-y-1 text-muted">
                    {picked.alt.map((a, i) => (
                      <li key={i}>{a.lead_time_days}-day lead time, {a.available_qty} in stock, {rupees(a.extra_cost_vs_current)} more than the current supplier</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="All suppliers" bodyClass="p-0"
        action={<input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Search suppliers" aria-label="Search suppliers"
          className="w-48 rounded-lg bg-raised px-3 py-1.5 text-[14px] text-ink placeholder:text-dim ring-1 ring-line focus:ring-signal" />}>
        {!suppliers ? <div className="p-4">{!error && <Loading />}</div> : !rows.length ? <div className="p-4"><Empty>No suppliers match.</Empty></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-[14px]">
              <thead className="border-b border-line text-[12.5px] text-dim">
                <tr><th className="px-4 py-2.5 font-medium">Supplier</th><th className="px-3 py-2.5 font-medium">City</th>
                  <th className="px-3 py-2.5 text-right font-medium">On time</th><th className="px-3 py-2.5 font-medium">Risk</th>
                  <th className="px-4 py-2.5 font-medium">Open POs (waiting orders)</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s) => (
                  <tr key={s.id} onClick={() => setSel(s.id)} aria-selected={sel === s.id}
                    className={`cursor-pointer hover:bg-raised ${sel === s.id ? "bg-raised" : ""}`}>
                    <td className="px-4 py-2.5"><button className="font-semibold hover:underline" onClick={() => setSel(s.id)}>{s.name}</button>
                      <span className="block text-[12.5px] text-dim">{s.id}</span></td>
                    <td className="px-3 py-2.5 text-muted">{s.city}</td>
                    <td className="px-3 py-2.5 text-right">{Math.round(s.reliability * 100)}%</td>
                    <td className="px-3 py-2.5"><Chip tone={band(s.reliability).tone}>{band(s.reliability).label}</Chip></td>
                    <td className="px-4 py-2.5">{s.pos.length ? s.pos.map((p) => `${p.id} (${p.status})`).join(", ") : <span className="text-dim">None</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

// Dot plot: position encodes the rate on a common 80–100% scale (a dot plot need not start at zero).
function ReliabilityPlot({ rows, sel, onPick }) {
  const W = 520, L = 170, R = 24, rowH = 46, top = 10;
  const H = top + rows.length * rowH + 34;
  const x = (v) => L + ((v - 0.8) / 0.2) * (W - L - R);
  const ticks = [0.8, 0.85, 0.9, 0.95, 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="On-time delivery rate by supplier">
      <rect x={x(0.8)} y={top} width={x(0.9) - x(0.8)} height={rows.length * rowH} fill="var(--color-crit)" fillOpacity="0.07" />
      <rect x={x(0.9)} y={top} width={x(0.95) - x(0.9)} height={rows.length * rowH} fill="var(--color-warn)" fillOpacity="0.07" />
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x(t)} x2={x(t)} y1={top} y2={top + rows.length * rowH} stroke="var(--color-line)" />
          <text x={x(t)} y={top + rows.length * rowH + 18} textAnchor="middle" className="fill-[var(--color-dim)] text-[11px]">{Math.round(t * 100)}%</text>
        </g>
      ))}
      {rows.map((s, i) => {
        const y = top + i * rowH + rowH / 2;
        const b = band(s.reliability);
        const on = sel === s.id;
        return (
          <g key={s.id} className="group cursor-pointer outline-none" onClick={() => onPick(s.id)} tabIndex={0} role="button"
            aria-label={`${s.name}: ${Math.round(s.reliability * 100)}% on time, ${b.label}`}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPick(s.id)}>
            <rect x={0} y={y - rowH / 2} width={W} height={rowH} fill={on ? "var(--color-raised)" : "transparent"} rx="6"
              className="group-focus-visible:stroke-[var(--color-signal)] group-focus-visible:[stroke-width:2]" />
            <text x={L - 12} y={y - 2} textAnchor="end" className="fill-[var(--color-ink)] text-[13px]">{s.name}</text>
            <text x={L - 12} y={y + 13} textAnchor="end" className="fill-[var(--color-dim)] text-[11px]">{b.label}</text>
            <line x1={x(0.8)} x2={x(s.reliability)} y1={y} y2={y} stroke="var(--color-line-strong)" strokeWidth="2" />
            <circle cx={x(s.reliability)} cy={y} r={on ? 8 : 6.5} fill="var(--color-data)" stroke="var(--color-deck)" strokeWidth="2" />
            <text x={x(s.reliability) + (s.reliability > 0.97 ? -12 : 12)} y={y + 4} textAnchor={s.reliability > 0.97 ? "end" : "start"}
              className="fill-[var(--color-ink)] text-[12px] font-semibold">{Math.round(s.reliability * 100)}%</text>
            {s.pos.some((p) => p.is_delayed) && (
              <text x={W - R} y={y + 4} textAnchor="end" className="fill-[var(--color-crit)] text-[11.5px] font-semibold">delay open</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
