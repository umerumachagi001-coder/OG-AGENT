import { STAGES, STAGE_STATE } from "../derive";
import { TONE } from "./ui";

/** Compact vertical stage list for one case (investigation sidebar). */
export function StageList({ states }) {
  return (
    <ol className="space-y-1.5">
      {STAGES.map((s, i) => {
        const st = STAGE_STATE[states[s.key]] || STAGE_STATE.pending;
        return (
          <li key={s.key} className="flex items-center gap-3 text-[14px]">
            <span className={`grid size-6 shrink-0 place-items-center rounded-full text-[12px] font-bold ${TONE[st.tone]}`} aria-hidden>
              {states[s.key] === "running" ? <span className="pulse-dot">●</span> : st.icon}
            </span>
            <span className="flex-1">{i + 1}. {s.label}</span>
            <span className="text-[12.5px] text-muted">{st.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

const EDGE = { done: "var(--color-good)", running: "var(--color-signal)", waiting: "var(--color-warn)", failed: "var(--color-crit)" };

/** The automation pipeline as connected nodes (workflow page). counts: optional per-stage totals across cases. */
export function PipelineGraph({ states = {}, counts, onPick }) {
  return (
    <ol className="canvas-grid grid gap-y-0 rounded-xl p-4 ring-1 ring-line sm:p-6 lg:grid-cols-7 lg:gap-x-7">
      {STAGES.map((s, i) => {
        const key = states[s.key];
        const st = STAGE_STATE[key] || STAGE_STATE.pending;
        const last = i === STAGES.length - 1;
        return (
          <li key={s.key} className="relative flex flex-col">
            <button type="button" onClick={() => onPick?.(s.key)}
              className={`relative z-10 h-full w-full rounded-xl bg-deck p-3 text-left ring-1 transition-colors hover:ring-line-strong lg:min-h-[170px] ${
                key === "waiting" ? "ring-warn/70" : key === "failed" ? "ring-crit/70" : key === "running" ? "ring-signal/70" : key === "done" ? "ring-good/50" : "ring-line"}`}>
              <span className="text-[11.5px] text-dim">Step {i + 1}</span>
              <span className="mt-0.5 block font-cond text-[16px] font-semibold leading-tight">{s.label}</span>
              <span className="mt-1 block text-[12px] leading-snug text-muted">{s.node}</span>
              {key && (
                <span className={`mt-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-medium ${TONE[st.tone]}`}>
                  {key === "running" ? <span className="pulse-dot">●</span> : <span aria-hidden>{st.icon}</span>} {st.label}
                </span>
              )}
              {counts && <span className="mt-2 block text-[12.5px] text-ink">{counts[s.key] ?? 0} case{counts[s.key] === 1 ? "" : "s"} here</span>}
            </button>
            {!last && (
              <span aria-hidden className="flex items-center justify-center max-lg:h-7 max-lg:w-full lg:absolute lg:-right-7 lg:top-1/2 lg:w-7 lg:-translate-y-1/2">
                <svg viewBox="0 0 24 24" className="size-5 max-lg:rotate-90" fill="none" stroke={EDGE[key] || "var(--color-line-strong)"} strokeWidth="2">
                  <path d="M4 12h14M13 7l5 5-5 5" />
                </svg>
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
