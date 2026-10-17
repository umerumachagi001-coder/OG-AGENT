import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { PipelineGraph } from "../components/Pipeline";
import { Chip, Empty, Loading, Panel, StatusChip } from "../components/ui";
import { STAGES, STAGE_STATE, stageStates } from "../derive";
import { DOMAIN, ago } from "../format";
import { useStore } from "../store";

function currentStage(states) {
  for (const s of STAGES) if (["running", "waiting", "failed", "pending"].includes(states[s.key])) return s.key;
  return "audit";
}

export default function Workflow() {
  const { exceptions, health } = useStore();
  const [trails, setTrails] = useState({});
  const [picked, setPicked] = useState(null);
  const key = exceptions.map((e) => `${e.id}:${e.status}`).join(",");

  useEffect(() => {
    let alive = true;
    const load = () => Promise.all(exceptions.map((e) => api.trail(e.id).then((t) => [e.id, t])))
      .then((pairs) => alive && setTrails(Object.fromEntries(pairs))).catch(() => {});
    load();
    const h = setInterval(load, 2500);
    return () => { alive = false; clearInterval(h); };
  }, [key]);

  const rows = useMemo(() => exceptions.slice().sort((a, b) => b.id - a.id).map((e) => {
    const st = trails[e.id] ? stageStates(e, trails[e.id]) : null;
    return { e, states: st, current: st ? currentStage(st) : null };
  }), [exceptions, trails]);

  const counts = useMemo(() => {
    const c = {};
    for (const r of rows) if (r.current) c[r.current] = (c[r.current] || 0) + 1;
    return c;
  }, [rows]);

  const sel = rows.find((r) => r.e.id === picked) || rows[0];
  const n8n = health?.engine === "n8n";

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-cond text-[30px] font-bold leading-tight">Workflow monitor</h1>
          <p className="max-w-[80ch] text-[14.5px] text-muted">
            The automation pipeline every exception goes through. Stage states come from each case's recorded steps.
          </p>
        </div>
        <EngineCard health={health} />
      </header>

      <Panel title={sel ? `Pipeline for ${sel.e.order_id} (case #${sel.e.id})` : "Pipeline"}
        action={sel && <StatusChip status={sel.e.status} />}>
        {!rows.length ? (
          <>
            <PipelineGraph />
            <p className="mt-3 text-[13.5px] text-muted">No live cases yet, so no stage has run. Trigger a failure to watch a case move through.</p>
          </>
        ) : !sel.states ? <Loading /> : <PipelineGraph states={sel.states} />}
        <p className="mt-3 text-[12.5px] text-dim">
          {n8n ? "Steps 1–5 run as the n8n workflows \"OEA 1 · Detect exceptions\" and \"OEA 2 · Investigate and resolve\". "
            : "The built-in Python engine is running these steps (n8n is not in use). "}
          Customer notification shows as completed only when an "email the customer" step actually ran; Slack pings to the ops channel
          aren't recorded here.
        </p>
      </Panel>

      <Panel title="Cases by current stage" bodyClass="p-0">
        {!rows.length ? <div className="p-4"><Empty>No live cases.</Empty></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[14px]">
              <thead className="border-b border-line text-[12.5px] text-dim">
                <tr><th className="px-4 py-2.5 font-medium">Case</th><th className="px-3 py-2.5 font-medium">Problem</th>
                  <th className="px-3 py-2.5 font-medium">Current stage</th><th className="px-3 py-2.5 font-medium">Stages</th>
                  <th className="px-4 py-2.5 font-medium">Detected</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => {
                  const cur = STAGES.find((s) => s.key === r.current);
                  const st = r.states ? STAGE_STATE[r.states[r.current]] : null;
                  return (
                    <tr key={r.e.id} onClick={() => setPicked(r.e.id)} aria-selected={sel?.e.id === r.e.id}
                      className={`cursor-pointer hover:bg-raised ${sel?.e.id === r.e.id ? "bg-raised" : ""}`}>
                      <td className="px-4 py-2.5"><a href={`#/case/${r.e.id}`} onClick={(ev) => ev.stopPropagation()} className="font-semibold hover:underline">{r.e.order_id}</a></td>
                      <td className="px-3 py-2.5 text-muted">{DOMAIN[r.e.domain]}</td>
                      <td className="px-3 py-2.5">{cur ? <>{cur.label} {st && <Chip tone={st.tone} className="ml-1">{st.label}</Chip>}</> : <Loading label="" />}</td>
                      <td className="px-3 py-2.5">
                        {r.states && (
                          <span className="flex gap-1" aria-label={STAGES.map((s) => `${s.label}: ${STAGE_STATE[r.states[s.key]].label}`).join(", ")}>
                            {STAGES.map((s) => {
                              const x = STAGE_STATE[r.states[s.key]];
                              return <span key={s.key} title={`${s.label}: ${x.label}`}
                                className={`grid size-5 place-items-center rounded text-[11px] ${{ good: "bg-good-wash text-good", warn: "bg-warn-wash text-warn",
                                  crit: "bg-crit-wash text-crit", live: "bg-signal-deep text-signal", muted: "bg-raised text-dim" }[x.tone]}`}>{x.icon}</span>;
                            })}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-muted">{ago(r.e.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Where cases are right now">
        <PipelineGraph counts={counts} />
      </Panel>
    </div>
  );
}

function EngineCard({ health }) {
  if (!health) return null;
  const n8n = health.engine === "n8n";
  return (
    <div className="rounded-xl bg-deck px-4 py-3 text-[13.5px] ring-1 ring-line">
      <p className="flex items-center gap-2">
        <span className={`inline-block size-2 rounded-full ${health.ok && (!n8n || health.n8n) ? "bg-good" : "bg-crit"}`} aria-hidden />
        <span className="font-medium">{n8n ? (health.n8n ? "n8n is connected" : "n8n is not answering") : "Python engine (n8n off)"}</span>
      </p>
      {n8n && health.n8n_editor && (
        <a href={health.n8n_editor} target="_blank" rel="noreferrer" className="mt-1 inline-block text-signal hover:underline">
          See executions in the n8n editor
        </a>
      )}
    </div>
  );
}
