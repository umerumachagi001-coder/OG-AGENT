"""Read endpoints the agent's investigation tools call (owner: P1, FR-5).

Tool → endpoint mapping (see docs/ARCHITECTURE.md):
    get_order            GET /orders/{order_id}
    check_inventory      GET /inventory/{sku}?location=WH-A
    get_supplier_status  GET /suppliers/status?po_id=PO-2001  |  ?sku=SKU-DESK  |  ?order_id=ORD-1002
    get_payment_status   GET /payments/{order_id}
    get_pricing_rules    GET /pricing/{sku}?promo_codes=FEST20,NEWUSER15&qty=1
                         GET /pricing/order/{order_id}
    get_sla              GET /sla/{order_id}
    find_similar_cases   GET /cases/similar?domain=inventory&symptom=pick+failed

Every endpoint accepts ?fail=timeout|error|slow.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from services.common import db, fail_flag, parse, row, rows, utcnow
from services.logic import price_lines, sla_view

router = APIRouter(dependencies=[Depends(fail_flag)])


def _404(what: str, key: str):
    raise HTTPException(404, detail={"error": "not_found", "message": f"{what} {key} not found"})


# ───────── Orders ─────────

@router.get("/orders", tags=["orders"])
def list_orders(status: str | None = None, limit: int = Query(100, le=500)):
    with db() as c:
        q = ("SELECT o.*, cu.name AS customer_name, cu.tier AS customer_tier FROM orders o"
             " JOIN customers cu ON cu.id=o.customer_id")
        args: list = []
        if status:
            q += " WHERE o.status IN (%s)" % ",".join("?" * len(status.split(",")))
            args += status.split(",")
        q += " ORDER BY o.created_at DESC LIMIT ?"
        out = rows(c.execute(q, args + [limit]))
        for o in out:
            o["promo_codes"] = json.loads(o["promo_codes"])
        return out


@router.get("/orders/{order_id}", tags=["orders"])
def get_order(order_id: str):
    with db() as c:
        o = row(c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
        if not o:
            _404("Order", order_id)
        o["promo_codes"] = json.loads(o["promo_codes"])
        o["customer"] = row(c.execute("SELECT * FROM customers WHERE id=?", (o["customer_id"],)).fetchone())
        o["items"] = rows(c.execute(
            "SELECT i.sku, p.name, p.category, i.qty, i.unit_price FROM order_items i"
            " JOIN products p ON p.sku=i.sku WHERE i.order_id=?", (order_id,)))
        o["status_history"] = rows(c.execute(
            "SELECT status, note, ts FROM order_status_history WHERE order_id=? ORDER BY id", (order_id,)))
        o["purchase_orders"] = rows(c.execute(
            "SELECT id, supplier_id, sku, qty, status, expected_arrival FROM purchase_orders WHERE for_order_id=?",
            (order_id,)))
        return o


# ───────── Inventory ─────────

@router.get("/inventory/{sku}", tags=["inventory"])
def check_inventory(sku: str, location: str | None = None):
    with db() as c:
        p = row(c.execute("SELECT sku, name FROM products WHERE sku=?", (sku,)).fetchone())
        if not p:
            _404("SKU", sku)
        q = ("SELECT i.warehouse_id, w.name AS warehouse_name, w.city, i.on_hand, i.reserved, i.last_scan_qty,"
             " i.last_scan_at, w.transfer_cost_per_unit FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id"
             " WHERE i.sku=?")
        args = [sku]
        if location:
            q += " AND i.warehouse_id=?"
            args.append(location)
        locs = rows(c.execute(q + " ORDER BY i.warehouse_id", args))
        for l in locs:
            l["available"] = l["on_hand"] - l["reserved"]
            # what can really be shipped: trust the lower of system and physical count
            l["verified_available"] = max(0, min(l["on_hand"], l["last_scan_qty"]) - l["reserved"])
            l["scan_mismatch"] = l["on_hand"] != l["last_scan_qty"]
        return {**p, "locations": locs,
                "total_on_hand": sum(l["on_hand"] for l in locs),
                "total_verified_available": sum(l["verified_available"] for l in locs)}


# ───────── Suppliers ─────────

@router.get("/suppliers", tags=["suppliers"])
def list_suppliers():
    with db() as c:
        return rows(c.execute("SELECT * FROM suppliers"))


@router.get("/suppliers/status", tags=["suppliers"])
def get_supplier_status(po_id: str | None = None, sku: str | None = None, order_id: str | None = None):
    if not (po_id or sku or order_id):
        raise HTTPException(422, detail={"error": "bad_request", "message": "Pass po_id, sku or order_id"})
    with db() as c:
        if po_id:
            pos = rows(c.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)))
            if not pos:
                _404("PO", po_id)
        elif order_id:
            pos = rows(c.execute("SELECT * FROM purchase_orders WHERE for_order_id=?", (order_id,)))
        else:
            pos = rows(c.execute("SELECT * FROM purchase_orders WHERE sku=? AND status!='received'", (sku,)))
        now = utcnow()
        for po in pos:
            po["supplier"] = row(c.execute("SELECT * FROM suppliers WHERE id=?", (po["supplier_id"],)).fetchone())
            catalog = row(c.execute("SELECT lead_time_days FROM supplier_catalog WHERE supplier_id=? AND sku=?",
                                    (po["supplier_id"], po["sku"])).fetchone()) or {}
            po["is_delayed"] = po["status"] == "delayed"
            po["days_until_arrival"] = round((parse(po["expected_arrival"]) - now).total_seconds() / 86400, 1)
            po["normal_lead_time_days"] = catalog.get("lead_time_days")
            po["alternates"] = rows(c.execute(
                "SELECT s.id AS supplier_id, s.name, s.city, s.reliability, sc.unit_cost, sc.lead_time_days,"
                " sc.available_qty, ROUND((sc.unit_cost - ?) * ?, 2) AS extra_cost_vs_current"
                " FROM supplier_catalog sc JOIN suppliers s ON s.id=sc.supplier_id"
                " WHERE sc.sku=? AND sc.supplier_id!=? AND sc.available_qty>=? ORDER BY sc.lead_time_days, sc.unit_cost",
                (po["unit_cost"], po["qty"], po["sku"], po["supplier_id"], po["qty"])))
        return {"purchase_orders": pos}


# ───────── Payments ─────────

@router.get("/payments/{order_id}", tags=["payments"])
def get_payment_status(order_id: str):
    with db() as c:
        o = c.execute("SELECT id, total, status FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            _404("Order", order_id)
        method = row(c.execute("SELECT * FROM payment_methods WHERE order_id=?", (order_id,)).fetchone())
        attempts = rows(c.execute("SELECT attempt_no, amount, status, decline_code, fraud_score, ts"
                                  " FROM payment_attempts WHERE order_id=? ORDER BY attempt_no", (order_id,)))
        signals = {}
        if method:
            if method.get("card_expiry"):
                y, m = map(int, method["card_expiry"].split("-"))
                now = utcnow()
                signals["card_expired"] = (y, m) < (now.year, now.month)
            signals["ip_billing_country_mismatch"] = method["ip_country"] != method["billing_country"]
        if attempts:
            signals["max_fraud_score"] = max(a["fraud_score"] for a in attempts)
            declines = [a for a in attempts if a["status"] == "declined"]
            signals["decline_count"] = len(declines)
            if len(declines) >= 2:
                span = (parse(declines[-1]["ts"]) - parse(declines[0]["ts"])).total_seconds() / 60
                signals["declines_within_minutes"] = round(span, 1)
        return {"order_id": order_id, "order_total": o["total"], "order_status": o["status"],
                "method": method, "attempts": attempts, "risk_signals": signals}


# ───────── Pricing ─────────

def _codes(promo_codes: str | None) -> list[str]:
    return [x for x in re.split(r"[,\s]+", promo_codes or "") if x]


@router.get("/pricing/order/{order_id}", tags=["pricing"])
def get_order_pricing(order_id: str):
    with db() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            _404("Order", order_id)
        lines = [(r["sku"], r["qty"]) for r in c.execute("SELECT sku, qty FROM order_items WHERE order_id=?", (order_id,))]
        p = price_lines(c, lines, json.loads(o["promo_codes"]))
        return {"order_id": order_id, "charged_total": o["total"], **p}


@router.get("/pricing/{sku}", tags=["pricing"])
def get_pricing_rules(sku: str, promo_codes: str | None = None, qty: int = 1):
    with db() as c:
        if not c.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
            _404("SKU", sku)
        return {"sku": sku, **price_lines(c, [(sku, qty)], _codes(promo_codes))}


@router.get("/promotions", tags=["pricing"])
def list_promotions():
    with db() as c:
        return rows(c.execute("SELECT * FROM promotions"))


# ───────── SLA ─────────

@router.get("/sla/{order_id}", tags=["sla"])
def get_sla(order_id: str):
    with db() as c:
        v = sla_view(c, order_id)
        if not v:
            _404("Order", order_id)
        return v


# ───────── Similar cases (FR-8) ─────────

_STOP = {"the", "a", "an", "at", "of", "to", "in", "on", "for", "and", "or", "is", "not", "shows", "system"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in _STOP and len(w) > 2}


@router.get("/cases/similar", tags=["cases"])
def find_similar_cases(domain: str | None = None, symptom: str | None = None, limit: int = 3):
    with db() as c:
        q = ("SELECT e.id AS exception_id, e.order_id, e.domain, e.severity, e.symptom, e.root_cause, e.confidence,"
             " e.created_at, pa.action_type, pa.params_json, pa.cost, r.outcome, r.resolved_by,"
             " r.human_feedback, r.minutes_to_resolve"
             " FROM exceptions e JOIN resolutions r ON r.exception_id=e.id"
             " LEFT JOIN proposed_actions pa ON pa.id=r.action_id")
        args = []
        if domain:
            q += " WHERE e.domain=?"
            args.append(domain)
        cases = rows(c.execute(q, args))
        want = _words(symptom or "")
        for x in cases:
            have = _words(x["symptom"]) | _words(x["root_cause"])
            x["similarity"] = round(len(want & have) / len(want), 2) if want else 0.0
            x["params"] = json.loads(x.pop("params_json") or "{}")
        cases.sort(key=lambda x: (x["similarity"], x["created_at"]), reverse=True)
        return {"query": {"domain": domain, "symptom": symptom}, "cases": cases[:limit]}


# ───────── Reference ─────────

@router.get("/warehouses", tags=["reference"])
def list_warehouses():
    with db() as c:
        return rows(c.execute("SELECT * FROM warehouses"))


@router.get("/carriers", tags=["reference"])
def list_carriers():
    with db() as c:
        return rows(c.execute("SELECT * FROM carriers"))
