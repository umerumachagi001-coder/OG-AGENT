import { forwardRef, useEffect, useRef } from "react";
import { SEVERITY, STATUS } from "../format";

export const TONE = {
  good: "bg-good-wash text-good ring-1 ring-good/40",
  warn: "bg-warn-wash text-warn ring-1 ring-warn/40",
  crit: "bg-crit-wash text-crit ring-1 ring-crit/40",
  live: "bg-signal-deep text-signal ring-1 ring-signal/40",
  muted: "bg-raised text-muted ring-1 ring-line",
};

export function Chip({ tone = "muted", children, pulse, className = "" }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12.5px] font-medium ${TONE[tone]} ${className}`}>
      {pulse && <span className="pulse-dot inline-block size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export function StatusChip({ status, investigating }) {
  const s = STATUS[status] || { label: status, tone: "muted" };
  return <Chip tone={s.tone} pulse={investigating || status === "investigating" || status === "open"}>{s.label}</Chip>;
}

export function SeverityTag({ severity, className = "" }) {
  const s = SEVERITY[severity] || SEVERITY.low;
  return (
    <span className={`inline-flex items-center gap-1.5 text-[13px] ${className}`}>
      <span aria-hidden className={`${s.cls} text-[11px]`}>{s.icon}</span>
      <span className="text-ink">{s.label}</span>
    </span>
  );
}

export const Button = forwardRef(function Button({ kind = "quiet", size = "md", className = "", ...p }, ref) {
  const k = {
    primary: "bg-signal text-night hover:bg-[#62dde0] disabled:bg-line disabled:text-dim font-semibold",
    approve: "bg-good text-night hover:brightness-110 disabled:bg-line disabled:text-dim font-semibold",
    warn: "bg-warn text-night hover:brightness-105 disabled:opacity-50 font-semibold",
    danger: "bg-transparent text-crit ring-1 ring-crit/50 hover:bg-crit-wash disabled:opacity-50",
    quiet: "bg-raised text-ink ring-1 ring-line hover:ring-line-strong disabled:opacity-50",
    ghost: "text-muted hover:text-ink",
  }[kind];
  const sz = size === "sm" ? "px-2.5 py-1.5 text-[13px]" : "px-3.5 py-2 text-[14px]";
  return (
    <button ref={ref} {...p}
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed ${sz} ${k} ${className}`} />
  );
});

export function Panel({ title, action, children, className = "", bodyClass = "p-4", id }) {
  return (
    <section id={id} className={`rounded-xl bg-deck ring-1 ring-line ${className}`} aria-label={typeof title === "string" ? title : undefined}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="font-cond text-[17px] font-semibold tracking-[0.01em]">{title}</h2>
          {action}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

export function Empty({ children, action }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-[14px] text-muted">
      <p>{children}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg bg-crit-wash px-3.5 py-2.5 text-[14px] text-crit ring-1 ring-crit/40">
      <span className="font-semibold">Couldn't load this.</span>
      <span className="text-ink/80">{String(error.message || error)}</span>
      {onRetry && <Button size="sm" kind="quiet" onClick={onRetry} className="ml-auto">Try again</Button>}
    </div>
  );
}

export function Loading({ label = "Loading…" }) {
  return (
    <p className="flex items-center gap-2 text-[14px] text-muted">
      <span className="pulse-dot inline-block size-2 rounded-full bg-signal" aria-hidden />{label}
    </p>
  );
}

export function RawJson({ data, label = "Show source data" }) {
  return (
    <details className="group mt-1">
      <summary className="cursor-pointer select-none text-[12.5px] text-dim hover:text-ink">
        <span className="group-open:hidden">{label}</span>
        <span className="hidden group-open:inline">Hide source data</span>
      </summary>
      <pre className="mt-1.5 max-h-80 overflow-auto rounded-lg bg-night p-3 text-[12px] leading-snug text-muted ring-1 ring-line">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

/** Modal confirmation for consequential actions. Esc or the backdrop cancels; focus starts on Cancel. */
export function ConfirmDialog({ open, title, children, confirmLabel, confirmKind = "primary", busy, busyLabel = "Sending…", onConfirm, onCancel }) {
  const cancelRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const esc = (e) => e.key === "Escape" && !busy && onCancel();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open, busy]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-night/75 p-4" onMouseDown={() => !busy && onCancel()}>
      <div role="dialog" aria-modal="true" aria-labelledby="dlg-title" onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-xl bg-deck p-5 shadow-[0_24px_60px_-20px_rgba(0,0,0,.8)] ring-1 ring-line-strong">
        <h2 id="dlg-title" className="font-cond text-[21px] font-bold">{title}</h2>
        <div className="mt-2 text-[14.5px] text-muted">{children}</div>
        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} kind="quiet" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button kind={confirmKind} onClick={onConfirm} disabled={busy}>{busy ? busyLabel : confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

export function Toast({ toast }) {
  if (!toast) return null;
  const t = { good: "bg-good text-night", crit: "bg-crit text-white", warn: "bg-warn text-night" }[toast.tone] || "bg-raised text-ink ring-1 ring-line-strong";
  return (
    <div role="status" className={`fixed bottom-5 left-1/2 z-50 max-w-[92vw] -translate-x-1/2 rounded-lg px-4 py-2.5 text-[14px] font-medium shadow-lg ${t}`}>
      {toast.text}
    </div>
  );
}

export function Stat({ value, label, hint, tone, onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick}
      className={`rounded-xl bg-deck px-4 py-3.5 text-left ring-1 ring-line ${onClick ? "hover:ring-line-strong" : ""}`}>
      <p className={`font-cond text-[30px] font-bold leading-none ${tone === "warn" ? "text-warn" : tone === "crit" ? "text-crit" : "text-ink"}`}>
        {value}
      </p>
      <p className="mt-1.5 text-[13.5px] text-ink">{label}</p>
      {hint && <p className="text-[12px] text-dim">{hint}</p>}
    </Tag>
  );
}
