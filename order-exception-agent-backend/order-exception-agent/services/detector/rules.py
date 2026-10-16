"""Detector (owner: P4, FR-1..FR-4).

Scans the source systems for orders that failed, stalled or are about to break, and writes one
`exceptions` row per (order, domain). The detector only *notices*; the agent investigates.

Rules (each returns candidate exceptions):
    pick_failed       orders.status = 'pick_failed'                           → inventory
    payment_failed    orders.status = 'payment_failed'                        → payment
    supplier_delay    open order waiting on a PO with status 'delayed'         → supplier
    sla_at_risk       open order whose ETA misses the promise (FR-4, predicted) → deadline
    margin_breach     unshipped order priced in violation of promo/margin rules → pricing
    stalled           non-terminal order with no status change for STALL_HOURS → deadline

Severity (FR-3) = domain base + order value + customer tier + time left on the promise.
"""
from __future__ import annotations

import json
from datetime import timedelta

from services.common import db, now_iso, parse, utcnow
from services.logic import price_lines, sla_view

OPEN = ("placed", "payment_pending", "paid", "allocated", "picking", "pick_failed",
        "awaiting_stock", "payment_failed", "packed")
STALL_HOURS = 48
# Don't open a second exception for the same order+domain while one is live, after a human
# rejected it, or within this many minutes of it being resolved.
REOPEN_AFTER_MIN = 5


def severity(value: float, tier: str, hours_left: float | None, extra: int = 0) -> str:
    pts = 1 + extra
    pts += 2 if value > 50000 else 1 if value > 15000 else 0
    pts += {"VIP": 2, "Gold": 1}.get(tier, 0)
    if hours_left is not None:
        pts += 2 if hours_left < 48 else 1 if hours_left < 96 else 0
    return "critical" if pts >= 6 else "high" if pts >= 4 else "medium" if pts >= 2 else "low"


def _candidates(c) -> list[dict]:
    out: list[dict] = []
    orders = [dict(r) for r in c.execute(
        "SELECT o.*, cu.tier FROM orders o JOIN customers cu ON cu.id=o.customer_id"
        f" WHERE o.status IN ({','.join('?' * len(OPEN))})", OPEN)]
    now = utcnow()
    for o in orders:
        oid = o["id"]
        sla = sla_view(c, oid)
        hours = sla["hours_left"] if sla else None
        sev = lambda extra=0: severity(o["total"], o["tier"], hours, extra)  # noqa: E731
        last = c.execute("SELECT note, ts FROM order_status_history WHERE order_id=? ORDER BY id DESC LIMIT 1",
                         (oid,)).fetchone()
        note = (last["note"] if last else None) or ""

        if o["status"] == "pick_failed":
            out.append({"order_id": oid, "domain": "inventory", "rule": "pick_failed", "severity": sev(),
                        "symptom": f"Pick failed at {o['warehouse_id']}. {note}".strip()})
        if o["status"] == "payment_failed":
            n = c.execute("SELECT COUNT(*) FROM payment_attempts WHERE order_id=? AND status='declined'", (oid,)).fetchone()[0]
            out.append({"order_id": oid, "domain": "payment", "rule": "payment_failed", "severity": sev(),
                        "symptom": f"Payment declined ({n} declined attempt{'s' if n != 1 else ''}). {note}".strip()})

        po = c.execute("SELECT * FROM purchase_orders WHERE for_order_id=? AND status='delayed'", (oid,)).fetchone()
        if po:
            out.append({"order_id": oid, "domain": "supplier", "rule": "supplier_delay", "severity": sev(1 if sla and sla["at_risk"] else 0),
                        "symptom": f"{po['id']} from {po['supplier_id']} delayed: {po['delay_reason']}."
                                   + (f" Delivery promise ({o['promised_date'][:10]}) at risk." if sla and sla["at_risk"] else "")})
        elif sla and sla["at_risk"] and o["status"] not in ("pick_failed", "payment_failed"):
            out.append({"order_id": oid, "domain": "deadline", "rule": "sla_at_risk", "severity": sev(1),
                        "symptom": f"ETA {sla['current_eta'][:10]} misses promised date {o['promised_date'][:10]}."})

        if o["status"] not in ("payment_failed",):
            lines = [(r["sku"], r["qty"]) for r in c.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (oid,))]
            p = price_lines(c, lines, json.loads(o["promo_codes"]))
            if p["violations"] or o["total"] < p["min_allowed_total"] - 0.01:
                below_cost = o["total"] < p["total_cost"]
                rules = ", ".join(v["rule"] for v in p["violations"]) or "below_margin_floor"
                out.append({"order_id": oid, "domain": "pricing", "rule": "margin_breach", "severity": sev(1 if below_cost else 0),
                            "symptom": f"Charged ₹{o['total']:,.2f}; margin floor needs ₹{p['min_allowed_total']:,.2f}"
                                       f"{' (below cost)' if below_cost else ''}. Rules: {rules}."})

        if last and now - parse(last["ts"]) > timedelta(hours=STALL_HOURS) and o["status"] not in ("pick_failed", "payment_failed"):
            out.append({"order_id": oid, "domain": "deadline", "rule": "stalled", "severity": sev(),
                        "symptom": f"No progress for {int((now - parse(last['ts'])).total_seconds() // 3600)} h in status '{o['status']}'."})
    return out


def _is_duplicate(c, order_id: str, domain: str) -> bool:
    r = c.execute(
        "SELECT status, resolved_at FROM exceptions WHERE order_id=? AND domain=? ORDER BY id DESC LIMIT 1",
        (order_id, domain)).fetchone()
    if not r:
        return False
    if r["status"] in ("open", "investigating", "awaiting_approval", "rejected", "failed"):
        return True
    return bool(r["resolved_at"]) and utcnow() - parse(r["resolved_at"]) < timedelta(minutes=REOPEN_AFTER_MIN)


def detect_once() -> list[dict]:
    """Run every rule once; insert new exceptions; return what was created."""
    created = []
    with db() as c:
        seen = set()
        for cand in _candidates(c):
            key = (cand["order_id"], cand["domain"])
            if key in seen or _is_duplicate(c, *key):
                continue
            seen.add(key)
            cur = c.execute(
                "INSERT INTO exceptions(order_id, domain, severity, status, symptom, created_at) VALUES (?,?,?,?,?,?)",
                (cand["order_id"], cand["domain"], cand["severity"], "open", cand["symptom"], now_iso()))
            created.append({"id": cur.lastrowid, **cand})
    return created


if __name__ == "__main__":
    for e in detect_once():
        print(e)
