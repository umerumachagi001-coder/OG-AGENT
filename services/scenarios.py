"""Failure scenarios (owner: P1). Triggered with POST /admin/trigger?fail=<scenario>.

Each scenario breaks one healthy demo order by changing the source-system data, exactly as the
real failure would. Nothing here creates an `exceptions` row — that is the detector's job (P4).

What the detector should notice for each one is listed in `detect_hint`.
"""
from __future__ import annotations

from datetime import date

from services.common import add_history, days_from_now, now_iso, set_order_status, utcnow
from services.logic import price_lines


def _sc1(conn):
    # Physical scan at WH-A finds 0 headphones; system of record still says 5. Picker fails.
    conn.execute("UPDATE inventory SET last_scan_qty=0, last_scan_at=? WHERE sku='SKU-HDPH' AND warehouse_id='WH-A'",
                 (days_from_now(-0.08),))
    set_order_status(conn, "ORD-1001", "pick_failed",
                     "Picker could not locate SKU-HDPH at bin A-14-03 (system shows 5 on hand)")


def _sc5(conn):
    # Inventory + deadline together: ORD-1001 was placed 2 days ago, so only 3 days remain on its promise when the
    # pick fails on phantom stock. Moving the stock alone is not enough: standard shipping (4 days + handling) is late.
    from datetime import timedelta
    from services.common import iso, parse
    shift = timedelta(days=2)
    o = conn.execute("SELECT created_at, promised_date FROM orders WHERE id='ORD-1001'").fetchone()
    conn.execute("UPDATE orders SET created_at=?, promised_date=? WHERE id='ORD-1001'",
                 (iso(parse(o["created_at"]) - shift), iso(parse(o["promised_date"]) - shift)))
    for h in conn.execute("SELECT id, ts FROM order_status_history WHERE order_id='ORD-1001'").fetchall():
        conn.execute("UPDATE order_status_history SET ts=? WHERE id=?", (iso(parse(h["ts"]) - shift), h["id"]))
    _sc1(conn)


def _sc2(conn):
    # SUP-1 production breakdown: PO-2001 slips 6 days, blowing the customer's promised date.
    conn.execute("UPDATE purchase_orders SET status='delayed', delay_reason=?, promised_ship_date=?, expected_arrival=?"
                 " WHERE id='PO-2001'",
                 ("Production line breakdown at Pune plant; new ship date confirmed by supplier",
                  days_from_now(5.5), days_from_now(7)))
    add_history(conn, "ORD-1002", "awaiting_stock", "Supplier SUP-1 pushed PO-2001 by 6 days")


def _decline(conn, order_id, attempts):
    conn.execute("DELETE FROM payment_attempts WHERE order_id=?", (order_id,))
    for n, (code, score, mins_ago) in enumerate(attempts, 1):
        conn.execute("INSERT INTO payment_attempts(order_id, attempt_no, amount, status, decline_code, fraud_score, ts)"
                     " SELECT id, ?, total, 'declined', ?, ?, ? FROM orders WHERE id=?",
                     (n, code, score, days_from_now(-mins_ago / 1440), order_id))


def _sc3_expired(conn):
    today = date.today()
    last_month = f"{today.year - (today.month == 1)}-{(today.month - 2) % 12 + 1:02d}"
    conn.execute("UPDATE payment_methods SET card_expiry=?, billing_country='IN', ip_country='IN' WHERE order_id='ORD-1003'",
                 (last_month,))
    _decline(conn, "ORD-1003", [("expired_card", 0.04, 6)])
    set_order_status(conn, "ORD-1003", "payment_failed", "Authorisation declined by issuer")


def _sc3_fraud(conn):
    conn.execute("UPDATE payment_methods SET card_expiry=?, billing_country='IN', ip_country='US' WHERE order_id='ORD-1003'",
                 (f"{utcnow().year + 2}-08",))
    _decline(conn, "ORD-1003", [("do_not_honor", 0.71, 9), ("do_not_honor", 0.84, 7), ("suspected_fraud", 0.91, 5)])
    set_order_status(conn, "ORD-1003", "payment_failed", "Authorisation declined by issuer (3 attempts in 4 min)")


def _sc4(conn):
    # Checkout bug lets non-stackable NEWUSER15 stack on FEST20 → monitor sold below cost.
    import json
    codes = ["FEST20", "NEWUSER15"]
    p = price_lines(conn, [("SKU-MNTR", 1)], codes)
    conn.execute("UPDATE orders SET promo_codes=?, discount=?, total=? WHERE id='ORD-1004'",
                 (json.dumps(codes), round(p["subtotal"] - p["price_as_applied"], 2), p["price_as_applied"]))
    conn.execute("UPDATE payment_attempts SET amount=? WHERE order_id='ORD-1004'", (p["price_as_applied"],))
    add_history(conn, "ORD-1004", "paid", "Promo codes FEST20 + NEWUSER15 accepted at checkout")


SCENARIOS = {
    "sc1": {
        "id": "SC-1", "order_id": "ORD-1001", "domain": "inventory", "apply": _sc1,
        "title": "Phantom stock",
        "summary": "System shows 5 headphones in WH-A, physical scan shows 0; pick fails. WH-B has 12.",
        "detect_hint": "orders.status = 'pick_failed'  (also: inventory.on_hand != last_scan_qty)",
        "expected": "check_inventory → find WH-B stock → reallocate_stock WH-A→WH-B (₹120, auto)",
    },
    "sc2": {
        "id": "SC-2", "order_id": "ORD-1002", "domain": "supplier", "apply": _sc2,
        "title": "Supplier misses ship date",
        "summary": "PO-2001 from SUP-1 slips 6 days; order promised in 6 days is now ~5 days late.",
        "detect_hint": "purchase_orders.status = 'delayed' for an open order, or GET /sla/{id} at_risk = true",
        "expected": "get_supplier_status → SUP-3 alternate (+₹2000, 2-day lead) + EXPRESS → escalate",
    },
    "sc3_expired": {
        "id": "SC-3a", "order_id": "ORD-1003", "domain": "payment", "apply": _sc3_expired,
        "title": "Payment declined — expired card",
        "summary": "Single decline, code expired_card, fraud score 0.04.",
        "detect_hint": "orders.status = 'payment_failed'",
        "expected": "get_payment_status → card expired, low fraud → notify_customer with payment link (auto)",
    },
    "sc3_fraud": {
        "id": "SC-3b", "order_id": "ORD-1003", "domain": "payment", "apply": _sc3_fraud,
        "title": "Payment declined — fraud signals",
        "summary": "3 declines in 4 minutes, fraud score up to 0.91, IP country ≠ billing country.",
        "detect_hint": "orders.status = 'payment_failed'",
        "expected": "get_payment_status → fraud pattern → hold_order + escalate (no retry)",
    },
    "sc4": {
        "id": "SC-4", "order_id": "ORD-1004", "domain": "pricing", "apply": _sc4,
        "title": "Discount stacking below margin",
        "summary": "FEST20 + non-stackable NEWUSER15 applied together; ₹18,999 monitor sold for ₹12,349 (cost ₹13,000).",
        "detect_hint": "GET /pricing/order/{id} has violations (margin check on paid/placed orders)",
        "expected": "get_pricing_rules → rule conflict → hold_order + propose apply_price_correction ₹15,199.20 → human",
    },
    "sc5": {
        "id": "SC-5", "order_id": "ORD-1001", "domain": "inventory", "apply": _sc5,
        "title": "Phantom stock with a tight deadline",
        "summary": "ORD-1001 placed 2 days ago hits phantom stock in WH-A with 3 days left; standard shipping would be 1.5 days late.",
        "detect_hint": "orders.status = 'pick_failed' and GET /sla/{id} at_risk = true (one case, both problems in the symptom)",
        "expected": "check_inventory + get_sla → reallocate WH-A→WH-B and upgrade to EXPRESS (₹470 total, auto)",
    },
}
# Short aliases so `?fail=sc3` works too.
ALIASES = {"sc3": "sc3_expired", "SC-1": "sc1", "SC-2": "sc2", "SC-3": "sc3_expired", "SC-4": "sc4", "SC-5": "sc5"}


def resolve(name: str) -> str | None:
    name = ALIASES.get(name, name)
    return name if name in SCENARIOS else None


def describe() -> list[dict]:
    return [{"name": k, **{f: v for f, v in s.items() if f != "apply"}} for k, s in SCENARIOS.items()]


def trigger(conn, name: str) -> dict:
    key = resolve(name)
    s = SCENARIOS[key]
    s["apply"](conn)
    return {"triggered": key, "at": now_iso(), **{f: v for f, v in s.items() if f != "apply"}}
