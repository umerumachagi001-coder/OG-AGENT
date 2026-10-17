"""Action endpoints the agent / orchestrator execute (owner: P1, FR-12).

These endpoints do NOT enforce approval policy — that's the policy engine's job (P2, FR-10).
They DO enforce physical/business reality (can't reallocate stock that isn't there, can't
price below the margin floor, a retry on an expired card fails) so a wrong agent decision
fails loudly instead of silently "working".

Every call is written to `action_log` (NFR-3). Responses share one shape:
    {"ok": bool, "action": str, "order_id": str, "cost": float, "message": str, ...details}
Business-rule failures return HTTP 200 with ok=false; malformed input returns 4xx.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.common import (add_history, db, days_from_now, iso, log_action, now_iso, parse, row,
                             set_order_status, utcnow)
from services.logic import HANDLING_DAYS, price_lines, sla_view, stock_ready_at

router = APIRouter(prefix="/actions", tags=["actions"])


def _order(c, order_id):
    o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        raise HTTPException(404, detail={"error": "not_found", "message": f"Order {order_id} not found"})
    return dict(o)


def _done(c, action, params, ok, message, cost=0.0, **extra):
    result = {"ok": ok, "action": action, "cost": round(cost, 2), "message": message, **extra}
    result["log_id"] = log_action(c, action, params, result, ok, cost)
    return result


# ───────── reallocate_stock ─────────

class Reallocate(BaseModel):
    order_id: str
    from_warehouse: str = Field(..., alias="from")
    to_warehouse: str = Field(..., alias="to")
    model_config = {"populate_by_name": True}


@router.post("/reallocate_stock")
def reallocate_stock(body: Reallocate):
    params = body.model_dump(by_alias=True)
    with db() as c:
        o = _order(c, body.order_id)
        if o["warehouse_id"] != body.from_warehouse:
            return _done(c, "reallocate_stock", params, False,
                         f"Order is allocated to {o['warehouse_id']}, not {body.from_warehouse}", order_id=body.order_id)
        to = c.execute("SELECT * FROM warehouses WHERE id=?", (body.to_warehouse,)).fetchone()
        if not to:
            raise HTTPException(422, detail={"error": "bad_request", "message": f"Unknown warehouse {body.to_warehouse}"})
        items = c.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (body.order_id,)).fetchall()
        short = []
        for it in items:
            inv = c.execute("SELECT * FROM inventory WHERE sku=? AND warehouse_id=?", (it["sku"], body.to_warehouse)).fetchone()
            avail = max(0, min(inv["on_hand"], inv["last_scan_qty"]) - inv["reserved"]) if inv else 0
            if avail < it["qty"]:
                short.append({"sku": it["sku"], "needed": it["qty"], "verified_available": avail})
        if short:
            return _done(c, "reallocate_stock", params, False, f"Not enough verified stock in {body.to_warehouse}",
                         order_id=body.order_id, shortfall=short)
        qty_total = 0
        for it in items:
            c.execute("UPDATE inventory SET reserved=MAX(0, reserved-?) WHERE sku=? AND warehouse_id=?",
                      (it["qty"], it["sku"], body.from_warehouse))
            c.execute("UPDATE inventory SET reserved=reserved+? WHERE sku=? AND warehouse_id=?",
                      (it["qty"], it["sku"], body.to_warehouse))
            qty_total += it["qty"]
        cost = qty_total * to["transfer_cost_per_unit"]
        c.execute("UPDATE orders SET warehouse_id=? WHERE id=?", (body.to_warehouse, body.order_id))
        set_order_status(c, body.order_id, "allocated",
                         f"Reallocated {body.from_warehouse} → {body.to_warehouse} by exception agent")
        sla = sla_view(c, body.order_id)
        return _done(c, "reallocate_stock", params, True,
                     f"Order now fulfilled from {to['name']}; {qty_total} unit(s) reserved there",
                     cost=cost, order_id=body.order_id, new_warehouse=body.to_warehouse,
                     new_eta=sla["current_eta"], meets_promise=not sla["at_risk"], previous_status=o["status"])


# ───────── switch_supplier ─────────

class SwitchSupplier(BaseModel):
    po_id: str
    supplier_id: str


@router.post("/switch_supplier")
def switch_supplier(body: SwitchSupplier):
    params = body.model_dump()
    with db() as c:
        po = c.execute("SELECT * FROM purchase_orders WHERE id=?", (body.po_id,)).fetchone()
        if not po:
            raise HTTPException(404, detail={"error": "not_found", "message": f"PO {body.po_id} not found"})
        if po["status"] in ("received", "cancelled"):
            return _done(c, "switch_supplier", params, False, f"PO is already {po['status']}", order_id=po["for_order_id"])
        cat = c.execute("SELECT * FROM supplier_catalog WHERE supplier_id=? AND sku=?", (body.supplier_id, po["sku"])).fetchone()
        if not cat or cat["available_qty"] < po["qty"]:
            return _done(c, "switch_supplier", params, False,
                         f"{body.supplier_id} cannot supply {po['qty']} × {po['sku']}", order_id=po["for_order_id"])
        new_id = "PO-" + str(int(c.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]) + 3000)
        arrival = days_from_now(cat["lead_time_days"])
        c.execute("UPDATE purchase_orders SET status='cancelled' WHERE id=?", (body.po_id,))
        c.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (new_id, po["sku"], body.supplier_id, po["qty"], cat["unit_cost"], po["for_order_id"],
                   days_from_now(max(cat["lead_time_days"] - 1, 0.2)), arrival, "open", None))
        c.execute("UPDATE supplier_catalog SET available_qty=available_qty-? WHERE supplier_id=? AND sku=?",
                  (po["qty"], body.supplier_id, po["sku"]))
        cost = (cat["unit_cost"] - po["unit_cost"]) * po["qty"]
        if po["for_order_id"]:
            add_history(c, po["for_order_id"], "awaiting_stock",
                        f"{body.po_id} ({po['supplier_id']}) cancelled; replaced by {new_id} from {body.supplier_id}")
        sla = sla_view(c, po["for_order_id"]) if po["for_order_id"] else None
        return _done(c, "switch_supplier", params, True,
                     f"{body.po_id} cancelled; {new_id} placed with {body.supplier_id}, arriving {arrival[:10]}",
                     cost=cost, order_id=po["for_order_id"], new_po_id=new_id, expected_arrival=arrival,
                     order_eta=sla and sla["current_eta"], meets_promise=sla and not sla["at_risk"])


# ───────── payments ─────────

class OrderOnly(BaseModel):
    order_id: str


@router.post("/retry_payment")
def retry_payment(body: OrderOnly):
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        if o["status"] not in ("payment_failed", "payment_pending"):
            return _done(c, "retry_payment", params, False, f"Order status is {o['status']}; nothing to retry",
                         order_id=body.order_id)
        attempts = c.execute("SELECT * FROM payment_attempts WHERE order_id=? ORDER BY attempt_no", (body.order_id,)).fetchall()
        if len(attempts) >= 4:
            return _done(c, "retry_payment", params, False, "Gateway limit: max 4 attempts per order", order_id=body.order_id)
        last = attempts[-1] if attempts else None
        m = c.execute("SELECT * FROM payment_methods WHERE order_id=?", (body.order_id,)).fetchone()
        now = utcnow()
        expired = False
        if m and m["card_expiry"]:
            y, mo = map(int, m["card_expiry"].split("-"))
            expired = (y, mo) < (now.year, now.month)
        fraud = last["fraud_score"] if last else 0.05
        if expired:
            status, code = "declined", "expired_card"
        elif fraud >= 0.5 or (last and last["decline_code"] == "suspected_fraud"):
            status, code, fraud = "declined", "suspected_fraud", min(0.99, fraud + 0.04)
        else:
            status, code = "captured", None
        n = (last["attempt_no"] if last else 0) + 1
        c.execute("INSERT INTO payment_attempts(order_id, attempt_no, amount, status, decline_code, fraud_score, ts)"
                  " VALUES (?,?,?,?,?,?,?)", (body.order_id, n, o["total"], status, code, fraud, now_iso()))
        if status == "captured":
            set_order_status(c, body.order_id, "paid", f"Payment captured on retry (attempt {n})")
            return _done(c, "retry_payment", params, True, "Payment captured", order_id=body.order_id, attempt_no=n)
        set_order_status(c, body.order_id, "payment_failed", f"Retry declined: {code}")
        return _done(c, "retry_payment", params, False, f"Retry declined: {code}", order_id=body.order_id,
                     attempt_no=n, decline_code=code)


class HoldOrder(BaseModel):
    order_id: str
    reason: str = "exception_review"


@router.post("/hold_order")
def hold_order(body: HoldOrder):
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        if o["status"] in ("shipped", "delivered", "cancelled"):
            return _done(c, "hold_order", params, False, f"Cannot hold an order that is {o['status']}", order_id=body.order_id)
        set_order_status(c, body.order_id, "on_hold", f"Held: {body.reason}")
        return _done(c, "hold_order", params, True, f"Order on hold ({body.reason})", order_id=body.order_id,
                     previous_status=o["status"])


class ReleaseOrder(BaseModel):
    order_id: str
    status: str | None = None


@router.post("/release_order")
def release_order(body: ReleaseOrder):
    """Undo a hold (helper for FR-15 rollback / reject flows)."""
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        if o["status"] != "on_hold":
            return _done(c, "release_order", params, False, "Order is not on hold", order_id=body.order_id)
        prev = c.execute("SELECT status FROM order_status_history WHERE order_id=? AND status!='on_hold' ORDER BY id DESC LIMIT 1",
                         (body.order_id,)).fetchone()
        new = body.status or (prev["status"] if prev else "placed")
        set_order_status(c, body.order_id, new, "Hold released")
        return _done(c, "release_order", params, True, f"Order released to {new}", order_id=body.order_id)


# ───────── pricing ─────────

class PriceCorrection(BaseModel):
    order_id: str
    price: float
    promo_codes: list[str] | None = None  # optional: the codes the corrected price is based on


@router.post("/apply_price_correction")
def apply_price_correction(body: PriceCorrection):
    import json
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        lines = [(r["sku"], r["qty"]) for r in c.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (body.order_id,))]
        p = price_lines(c, lines, json.loads(o["promo_codes"]))
        if body.price < p["min_allowed_total"]:
            return _done(c, "apply_price_correction", params, False,
                         f"₹{body.price:,.2f} is below the margin floor (min ₹{p['min_allowed_total']:,.2f})",
                         order_id=body.order_id, min_allowed_total=p["min_allowed_total"])
        if body.price > p["subtotal"]:
            return _done(c, "apply_price_correction", params, False, "Price above list price", order_id=body.order_id)
        old = o["total"]
        codes = body.promo_codes if body.promo_codes is not None else json.loads(o["promo_codes"])
        c.execute("UPDATE orders SET total=?, discount=?, promo_codes=? WHERE id=?",
                  (round(body.price, 2), round(p["subtotal"] - body.price, 2), json.dumps(codes), body.order_id))
        set_order_status(c, body.order_id, "payment_pending",
                         f"Price corrected ₹{old:,.2f} → ₹{body.price:,.2f}; awaiting customer re-authorisation")
        return _done(c, "apply_price_correction", params, True, f"Price corrected to ₹{body.price:,.2f}",
                     order_id=body.order_id, old_total=old, new_total=round(body.price, 2),
                     revenue_recovered=round(body.price - old, 2))


# ───────── shipping ─────────

class Expedite(BaseModel):
    order_id: str
    carrier: str = "EXPRESS"


@router.post("/expedite_shipping")
def expedite_shipping(body: Expedite):
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        car = c.execute("SELECT * FROM carriers WHERE id=?", (body.carrier,)).fetchone()
        if not car:
            raise HTTPException(422, detail={"error": "bad_request", "message": f"Unknown carrier {body.carrier}"})
        if o["status"] in ("shipped", "delivered", "cancelled"):
            return _done(c, "expedite_shipping", params, False, f"Order already {o['status']}", order_id=body.order_id)
        c.execute("UPDATE orders SET carrier=? WHERE id=?", (body.carrier, body.order_id))
        add_history(c, body.order_id, o["status"], f"Carrier upgraded {o['carrier']} → {body.carrier}")
        sla = sla_view(c, body.order_id)
        return _done(c, "expedite_shipping", params, True, f"Shipping upgraded to {car['name']}",
                     cost=car["base_cost"], order_id=body.order_id, new_eta=sla["current_eta"],
                     meets_promise=not sla["at_risk"], previous_carrier=o["carrier"])


# ───────── customer comms (FR-14) ─────────

class Notify(BaseModel):
    order_id: str
    message: str
    channel: str = "email"


@router.post("/notify_customer")
def notify_customer(body: Notify):
    params = body.model_dump()
    with db() as c:
        o = _order(c, body.order_id)
        cust = c.execute("SELECT name, email FROM customers WHERE id=?", (o["customer_id"],)).fetchone()
        cur = c.execute("INSERT INTO notifications(order_id, channel, message, ts) VALUES (?,?,?,?)",
                        (body.order_id, body.channel, body.message, now_iso()))
        add_history(c, body.order_id, o["status"], f"Customer notified via {body.channel}")
        return _done(c, "notify_customer", params, True, f"Sent to {cust['name']} <{cust['email']}>",
                     order_id=body.order_id, notification_id=cur.lastrowid)


# ───────── undo (FR-15) ─────────
# Reverses one earlier action from the action log, restoring the state it changed. Only actions whose
# effect can be taken back are undoable; the system refuses if the order has moved on since.

UNDOABLE = {"reallocate_stock", "expedite_shipping", "hold_order"}
NOT_UNDOABLE_WHY = {
    "notify_customer": "the customer has already been sent the message",
    "retry_payment": "the payment was captured; taking it back is a refund, which needs approval",
    "switch_supplier": "the replacement purchase order has been placed with the supplier",
    "apply_price_correction": "the customer has been asked to re-authorise the new price",
    "release_order": "release an order by holding it again instead",
}


class Undo(BaseModel):
    log_id: int
    reason: str | None = None


@router.post("/undo")
def undo(body: Undo):
    import json
    params = body.model_dump()
    with db() as c:
        # Take the write lock first, so two undo requests at the same moment can't both pass the
        # "already undone?" check and reverse the action twice.
        c.execute("BEGIN IMMEDIATE")
        entry = c.execute("SELECT * FROM action_log WHERE id=?", (body.log_id,)).fetchone()
        if not entry:
            raise HTTPException(404, detail={"error": "not_found", "message": f"Action log entry {body.log_id} not found"})
        kind, p, r = entry["action_type"], json.loads(entry["params_json"]), json.loads(entry["result_json"])
        oid = r.get("order_id") or p.get("order_id")
        params.update(action=kind, order_id=oid)
        if not entry["ok"]:
            return _done(c, "undo", params, False, "That action did not succeed, so there is nothing to undo", order_id=oid)
        if kind not in UNDOABLE:
            why = NOT_UNDOABLE_WHY.get(kind, "this kind of action cannot be reversed")
            return _done(c, "undo", params, False, f"{kind.replace('_', ' ').capitalize()} can't be undone: {why}", order_id=oid)
        done_before = c.execute("SELECT 1 FROM action_log WHERE action_type='undo' AND ok=1"
                                " AND json_extract(params_json, '$.log_id')=?", (body.log_id,)).fetchone()
        if done_before:
            return _done(c, "undo", params, False, "Already undone", order_id=oid)
        o = _order(c, oid)
        if o["status"] in ("shipped", "delivered", "cancelled"):
            return _done(c, "undo", params, False, f"Too late to undo: the order is {o['status']}", order_id=oid)
        note = f"Undo: {body.reason}" if body.reason else "Undo"

        if kind == "reallocate_stock":
            src, dst = p["from"], p["to"]
            if o["warehouse_id"] != dst:
                return _done(c, "undo", params, False, f"Order has moved on: it is now allocated to {o['warehouse_id']}", order_id=oid)
            for it in c.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (oid,)).fetchall():
                c.execute("UPDATE inventory SET reserved=MAX(0, reserved-?) WHERE sku=? AND warehouse_id=?", (it["qty"], it["sku"], dst))
                c.execute("UPDATE inventory SET reserved=reserved+? WHERE sku=? AND warehouse_id=?", (it["qty"], it["sku"], src))
            c.execute("UPDATE orders SET warehouse_id=? WHERE id=?", (src, oid))
            back = r.get("previous_status") or "allocated"
            set_order_status(c, oid, back, f"{note}. Reallocation {src} → {dst} reversed; transfer cancelled before dispatch")
            return _done(c, "undo", params, True, f"Reallocation reversed: order is back on {src} ({back.replace('_', ' ')})",
                         cost=-float(entry["cost"]), order_id=oid, restored_status=back, restored_warehouse=src)

        if kind == "expedite_shipping":
            prev = r.get("previous_carrier")
            if not prev:
                return _done(c, "undo", params, False, "No record of the previous carrier", order_id=oid)
            if o["carrier"] != p.get("carrier"):
                return _done(c, "undo", params, False, f"Order has moved on: carrier is now {o['carrier']}", order_id=oid)
            c.execute("UPDATE orders SET carrier=? WHERE id=?", (prev, oid))
            add_history(c, oid, o["status"], f"{note}. Carrier upgrade reversed {o['carrier']} → {prev}")
            return _done(c, "undo", params, True, f"Shipping upgrade reversed: back to {prev}",
                         cost=-float(entry["cost"]), order_id=oid, restored_carrier=prev)

        # hold_order
        if o["status"] != "on_hold":
            return _done(c, "undo", params, False, "Order is no longer on hold", order_id=oid)
        back = r.get("previous_status") or "placed"
        set_order_status(c, oid, back, f"{note}. Hold released")
        return _done(c, "undo", params, True, f"Hold released: order is back to {back.replace('_', ' ')}",
                     order_id=oid, restored_status=back)
