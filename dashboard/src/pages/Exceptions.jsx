import { useMemo, useState } from "react";
import { Empty, Loading, Panel, SeverityTag, StatusChip } from "../components/ui";
import { DOMAIN, OPEN_STATUSES, SEVERITY, STATUS, ago, rupees } from "../format";
import { go, useStore } from "../store";

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "open", label: "Open", test: (e) => OPEN_STATUSES.includes(e.status) },
  { key: "decision", label: "Needs decision", test: (e) => e.status === "awaiting_approval" },
  { key: "closed", label: "Closed", test: (e) => !OPEN_STATUSES.includes(e.status) },
];

const SORTS = {
  priority: (a, b) => (OPEN_STATUSES.includes(b.status) - OPEN_STATUSES.includes(a.status))
    || (SEVERITY[a.severity]?.rank ?? 9) - (SEVERITY[b.severity]?.rank ?? 9) || b.id - a.id,
  newest: (a, b) => b.id - a.id,
  value: (a, b) => b.order_value - a.order_value,
  severity: (a, b) => (SEVERITY[a.severity]?.rank ?? 9) - (SEVERITY[b.severity]?.rank ?? 9) || b.id - a.id,
};

export default function Exceptions({ query }) {
  const { exceptions, loaded } = useStore();
  const [filter, setFilter] = useState(query?.get("filter") || "all");
  const [domain, setDomain] = useState("any");
  const [sort, setSort] = useState("priority");
  const [term, setTerm] = useState("");

  const rows = useMemo(() => {
    const f = FILTERS.find((x) => x.key === filter) || FILTERS[0];
    const t = term.trim().toLowerCase();
    return exceptions
      .filter(f.test)
      .filter((e) => domain === "any" || e.domain === domain)
      .filter((e) => !t || [e.order_id, e.customer_name, e.symptom, e.root_cause].some((v) => (v || "").toLowerCase().includes(t)))
      .sort(SORTS[sort]);
  }, [exceptions, filter, domain, sort, term]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-cond text-[30px] font-bold leading-tight">Exceptions</h1>
        <p className="text-[14.5px] text-muted">Every problem the automation has detected this session. Open one to see the full investigation.</p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Status filter" className="flex rounded-lg bg-deck p-1 ring-1 ring-line">
          {FILTERS.map((f) => (
            <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key}
              className={`rounded-md px-3 py-1.5 text-[13.5px] ${filter === f.key ? "bg-signal-deep text-ink" : "text-muted hover:text-ink"}`}>
              {f.label} <span className="text-dim">{exceptions.filter(f.test).length}</span>
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-[13.5px] text-muted">Problem type
          <select value={domain} onChange={(e) => setDomain(e.target.value)} className="rounded-lg bg-deck px-2.5 py-1.5 text-ink ring-1 ring-line">
            <option value="any">Any</option>
            {Object.entries(DOMAIN).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-[13.5px] text-muted">Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="rounded-lg bg-deck px-2.5 py-1.5 text-ink ring-1 ring-line">
            <option value="priority">Priority</option><option value="newest">Newest</option>
            <option value="severity">Severity</option><option value="value">Order value</option>
          </select>
        </label>
        <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Search order, customer, cause"
          aria-label="Search exceptions"
          className="ml-auto min-w-[220px] rounded-lg bg-deck px-3 py-1.5 text-[14px] text-ink placeholder:text-dim ring-1 ring-line focus:ring-signal" />
      </div>

      <Panel bodyClass="p-0">
        {!loaded ? <div className="p-4"><Loading /></div> : !rows.length ? (
          <div className="p-4"><Empty>{exceptions.length ? "No exceptions match these filters." : "No exceptions yet. Use Trigger a failure to create one."}</Empty></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[14px]">
              <thead className="border-b border-line text-[12.5px] text-dim">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Order</th>
                  <th className="px-3 py-2.5 font-medium">Severity</th>
                  <th className="px-3 py-2.5 font-medium">Issue</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-3 py-2.5 font-medium">Next step</th>
                  <th className="px-4 py-2.5 text-right font-medium">Value</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => (
                  <tr key={e.id} onClick={() => go(`case/${e.id}`)} className="cursor-pointer hover:bg-raised">
                    <td className="px-4 py-3">
                      <a href={`#/case/${e.id}`} onClick={(ev) => ev.stopPropagation()} className="font-cond text-[16px] font-semibold hover:underline">{e.order_id}</a>
                      <p className="text-[12.5px] text-dim">{e.customer_name}, {ago(e.created_at)}</p>
                    </td>
                    <td className="px-3 py-3"><SeverityTag severity={e.severity} /></td>
                    <td className="max-w-[340px] px-3 py-3">
                      <p className="text-muted">{DOMAIN[e.domain]}</p>
                      <p className="truncate" title={e.root_cause || e.symptom}>{e.root_cause || e.symptom}</p>
                    </td>
                    <td className="px-3 py-3"><StatusChip status={e.status} /></td>
                    <td className="px-3 py-3 text-muted">{STATUS[e.status]?.next}</td>
                    <td className="px-4 py-3 text-right">{rupees(e.order_value)}</td>
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
