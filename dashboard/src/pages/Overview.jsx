import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import OrderOrbit from "../components/OrderOrbit";
import { Button, Empty, Loading, Panel, SeverityTag, Stat, StatusChip } from "../components/ui";
import { auditEvents } from "../derive";
import { DOMAIN, OPEN_STATUSES, SEVERITY, STATUS, ago, rupees, rupeesShort } from "../format";
import { go, useStore } from "../store";

const sortQueue = (a, b) => {
  const oa = OPEN_STATUSES.includes(a.status), ob = OPEN_STATUSES.includes(b.status);
  if (oa !== ob) return oa ? -1 : 1;
  const wa = a.status === "awaiting_approval", wb = b.status === "awaiting_approval";
  if (wa !== wb) return wa ? -1 : 1;
  return (SEVERITY[a.severity]?.rank ?? 9) - (SEVERITY[b.severity]?.rank ?? 9) || b.id - a.id;
};

export default function Overview() {
  const { exceptions, metrics, orders, health, loaded } = useStore();
  const queue = useMemo(() => [...exceptions].sort(sortQueue), [exceptions]);
  const open = exceptions.filter((e) => OPEN_STATUSES.includes(e.status));
  const highRisk = open.filter((e) => e.severity === "high" || e.severity === "critical");
  const investigating = exceptions.filter((e) => e.status === "open" || e.status === "investigating");
  const waiting = exceptions.filter((e) => e.status === "awaiting_approval");
  const liveResolved = exceptions.filter((e) => ["auto_resolved", "resolved"].includes(e.status)).length;
  const history = metrics ? metrics.resolved - liveResolved : 0;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-cond text-[30px] font-bold leading-tight">Operations overview</h1>
          <p className="text-[14.5px] text-muted">
            Every order the automation is watching, and what needs a person right now.
          </p>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat value={health?.services?.orders ?? "—"} label="Orders monitored" hint="Live from the order system" />
        <Stat value={highRisk.length} label="High-risk orders open" hint="High or critical severity" tone={highRisk.length ? "crit" : undefined}
          onClick={() => go("exceptions?filter=open")} />
        <Stat value={waiting.length} label="Awaiting your approval" hint="Fixes the policy won't run alone" tone={waiting.length ? "warn" : undefined}
          onClick={() => go("approvals")} />
        <Stat value={investigating.length} label="Active investigations" hint="Being diagnosed now" onClick={() => go("workflow")} />
        <Stat value={liveResolved} label="Resolved this session"
          hint={history > 0 ? `Plus ${history} past cases in the demo history` : "Fixed automatically or after approval"} />
      </div>

      <Impact m={metrics} history={history} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <Panel title="Order network" action={<span className="text-[12.5px] text-dim">Click a labelled order to investigate</span>}>
          {!loaded ? <Loading /> : orders.length ? (
            <OrderOrbit orders={orders} exceptions={exceptions} onOpen={(id) => go(`case/${id}`)} />
          ) : <Empty>No orders came back from the order system.</Empty>}
        </Panel>

        <div className="space-y-5">
          <PendingDecisions items={waiting} />
          <Panel title="Priority queue" bodyClass="p-0"
            action={<Button size="sm" kind="ghost" onClick={() => go("exceptions")}>All exceptions</Button>}>
            {!loaded ? <div className="p-4"><Loading /></div> : queue.length ? (
              <ul className="divide-y divide-line">
                {queue.slice(0, 6).map((e) => <QueueRow key={e.id} e={e} />)}
              </ul>
            ) : (
              <div className="p-4"><Empty>No exceptions. Every order is moving. Use Trigger a failure to watch the automation handle one.</Empty></div>
            )}
          </Panel>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <ActivityFeed exceptions={exceptions} />
        <ByType />
      </div>
    </div>
  );
}

const duration = (min) => (min == null ? "—" : min < 1 ? `${Math.max(1, Math.round(min * 60))} s` : `${Math.round(min)} min`);
const saved = (min) => (min >= 60 ? `${(min / 60).toFixed(1)} h` : `${Math.round(min)} min`);

// FR-19: the business impact, straight from GET /metrics. The manual baseline is an assumption, and says so.
function Impact({ m, history }) {
  if (!m) return null;
  return (
    <Panel title="Impact" action={<span className="text-[12.5px] text-dim">All resolved cases{history > 0 ? `, incl. ${history} from the demo history` : ""}</span>}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        <div>
          <dt className="text-[13.5px] text-muted">Fixed without a person</dt>
          <dd className="font-cond text-[30px] font-bold leading-tight text-good">{m.resolved ? `${Math.round(m.auto_resolved_pct)}%` : "—"}</dd>
          <dd className="text-[12px] text-dim">{m.auto_resolved} of {m.resolved} resolved cases</dd>
        </div>
        <div>
          <dt className="text-[13.5px] text-muted">Average time to fix</dt>
          <dd className="font-cond text-[30px] font-bold leading-tight">{duration(m.avg_minutes_auto)}</dd>
          <dd className="text-[12px] text-dim">when automatic; {duration(m.avg_minutes_human)} when a person approves</dd>
        </div>
        <div>
          <dt className="text-[13.5px] text-muted">Analyst time saved</dt>
          <dd className="font-cond text-[30px] font-bold leading-tight">{saved(m.analyst_minutes_saved)}</dd>
          <dd className="text-[12px] text-dim">assumes {m.manual_baseline_minutes} min per case done by hand</dd>
        </div>
        <div>
          <dt className="text-[13.5px] text-muted">Order value protected</dt>
          <dd className="font-cond text-[30px] font-bold leading-tight">{rupeesShort(m.revenue_protected)}</dd>
          <dd className="text-[12px] text-dim">for {rupees(m.action_cost_spent)} spent on fixes</dd>
        </div>
      </dl>
    </Panel>
  );
}

function QueueRow({ e }) {
  return (
    <li>
      <a href={`#/case/${e.id}`} className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-1.5 px-4 py-3 hover:bg-raised sm:grid-cols-[auto_1fr_auto]">
        <SeverityTag severity={e.severity} className="w-20" />
        <div className="min-w-0">
          <p className="sm:truncate">
            <span className="font-cond text-[16px] font-semibold">{e.order_id}</span>
            <span className="text-muted"> {DOMAIN[e.domain]} problem, {e.customer_name}, {rupees(e.order_value)}</span>
          </p>
          <p className="truncate text-[13px] text-dim">Next: {STATUS[e.status]?.next}</p>
        </div>
        <span className="col-start-2 sm:col-start-auto"><StatusChip status={e.status} /></span>
      </a>
    </li>
  );
}

function PendingDecisions({ items }) {
  if (!items.length) return null;
  return (
    <section className="overflow-hidden rounded-xl bg-warn-wash/60 ring-1 ring-warn/50" aria-label="Pending decisions">
      <div className="hazard-edge h-1.5" aria-hidden />
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <p className="font-cond text-[18px] font-bold text-warn">
          {items.length} {items.length === 1 ? "fix is" : "fixes are"} waiting for your decision
        </p>
        <div className="flex flex-wrap gap-2">
          {items.slice(0, 4).map((e) => (
            <a key={e.id} href={`#/case/${e.id}`}
              className="rounded-lg bg-night/60 px-2.5 py-1 text-[13.5px] ring-1 ring-warn/40 hover:ring-warn">
              {e.order_id}, {DOMAIN[e.domain].toLowerCase()}
            </a>
          ))}
        </div>
        <Button kind="warn" size="sm" className="ml-auto" onClick={() => go("approvals")}>Open approvals</Button>
      </div>
    </section>
  );
}

function ActivityFeed({ exceptions }) {
  const [events, setEvents] = useState(null);
  const ids = exceptions.slice().sort((a, b) => b.id - a.id).slice(0, 6).map((e) => e.id).join(",");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const list = exceptions.filter((e) => ids.split(",").includes(String(e.id)));
      try {
        const trails = await Promise.all(list.map((e) => api.trail(e.id)));
        if (!alive) return;
        setEvents(list.flatMap((e, i) => auditEvents(e, trails[i])).sort((a, b) => b.ts.localeCompare(a.ts) || b.step - a.step).slice(0, 8));
      } catch { if (alive) setEvents([]); }
    };
    load();
    const h = setInterval(load, 4000);
    return () => { alive = false; clearInterval(h); };
  }, [ids]);

  return (
    <Panel title="Recent activity" action={<a href="#/audit" className="text-[13px] text-signal hover:underline">Full audit trail</a>}>
      {events == null ? <Loading /> : events.length ? (
        <ol className="space-y-3">
          {events.map((ev) => (
            <li key={`${ev.exception_id}-${ev.step}`} className="grid grid-cols-[4.5rem_1fr] gap-3 text-[13.5px]">
              <span className="text-dim">{ago(ev.ts)}</span>
              <span>
                <a href={`#/case/${ev.exception_id}`} className="font-semibold hover:underline">{ev.order_id}</a>
                <span className="text-muted"> {ev.actor}: </span>
                <span className="text-ink/90">{ev.text}</span>
              </span>
            </li>
          ))}
        </ol>
      ) : <Empty>Nothing has happened yet this session.</Empty>}
    </Panel>
  );
}

// Exceptions by problem type, all recorded cases (live + the seeded demo history). One series, one colour.
function ByType() {
  const [all, setAll] = useState(null);
  const [error, setError] = useState(null);
  const { exceptions } = useStore();
  useEffect(() => {
    api.exceptions(true).then((d) => { setAll(d); setError(null); }).catch(setError);
  }, [exceptions.length]);
  const rows = useMemo(() => {
    if (!all) return [];
    return Object.keys(DOMAIN).map((k) => ({ k, label: DOMAIN[k], n: all.filter((e) => e.domain === k).length,
      live: all.filter((e) => e.domain === k && e.origin === "live").length }));
  }, [all]);
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <Panel title="Exceptions by problem type" action={<span className="text-[12.5px] text-dim">All recorded cases, incl. demo history</span>}>
      {error ? <p className="text-crit">{error.message}</p> : !all ? <Loading /> : (
        <div className="space-y-2.5" role="table" aria-label="Exceptions by problem type">
          {rows.map((r) => (
            <div key={r.k} role="row" className="group grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-3 text-[13.5px]"
              title={`${r.label}: ${r.n} cases (${r.live} this session)`}>
              <span role="cell" className="text-muted">{r.label}</span>
              <span role="cell" className="h-4 rounded-r-[4px] bg-data group-hover:brightness-110"
                style={{ width: `${(r.n / max) * 100}%`, minWidth: r.n ? 4 : 0 }} />
              <span role="cell" className="text-right">{r.n}{r.live ? <span className="text-dim"> ({r.live} new)</span> : null}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
