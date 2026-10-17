import { Fragment, useEffect, useState } from "react";
import { api } from "../api";
import { ErrorNote, Loading, Panel, RawJson } from "../components/ui";
import { dateTime, rupees, shortDate } from "../format";

// An order with no exception: shown straight from the order system so search always lands somewhere true.
export default function Order({ id }) {
  const [o, setO] = useState(null);
  const [error, setError] = useState(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    setO(null);
    api.order(id).then((x) => { setO(x); setError(null); }).catch(setError);
  }, [id, n]);
  if (error) return <ErrorNote error={error} onRetry={() => setN((x) => x + 1)} />;
  if (!o) return <Loading label={`Loading ${id}…`} />;
  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-[13px] text-dim"><a href="#/overview" className="hover:text-ink">Overview</a> / {o.id}</nav>
      <header>
        <h1 className="font-cond text-[30px] font-bold">{o.id}</h1>
        <p className="text-muted">No exception has been detected for this order. It is moving normally.</p>
      </header>
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Order record">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[14px]">
            <dt className="text-muted">Customer</dt><dd>{o.customer.name}, {o.customer.tier}, {o.customer.city}</dd>
            <dt className="text-muted">Status</dt><dd>{o.status.replaceAll("_", " ")}</dd>
            <dt className="text-muted">Charged</dt><dd>{rupees(o.total, true)}</dd>
            <dt className="text-muted">Promised by</dt><dd>{shortDate(o.promised_date)}</dd>
            {o.warehouse_id && <><dt className="text-muted">Ships from</dt><dd>{o.warehouse_id}</dd></>}
            {o.items.map((it) => (<Fragment key={it.sku}><dt className="text-muted">Item</dt><dd>{it.qty} × {it.name}</dd></Fragment>))}
          </dl>
          <RawJson data={o} />
        </Panel>
        <Panel title="Status history">
          <ol className="space-y-1.5 text-[14px]">
            {o.status_history.map((h, i) => (
              <li key={i} className="grid grid-cols-[9rem_1fr] gap-3">
                <span className="text-dim">{dateTime(h.ts)}</span>
                <span>{h.status.replaceAll("_", " ")}{h.note ? <span className="text-muted">: {h.note}</span> : null}</span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}
