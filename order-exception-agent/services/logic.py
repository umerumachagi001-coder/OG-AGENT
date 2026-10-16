"""Business logic shared by endpoints and the seed script (owner: P1).

Kept free of FastAPI so P2/P4 can import it in tests if useful.
"""
from __future__ import annotations

from datetime import timedelta
from itertools import combinations

from services.common import iso, parse, utcnow

HANDLING_DAYS = 0.5  # pick + pack time once stock is in the warehouse


# ───────────────────────── Pricing ─────────────────────────

def _promos(conn, codes):
    found, unknown = [], []
    for c in codes:
        r = conn.execute("SELECT * FROM promotions WHERE code=? AND active=1", (c,)).fetchone()
        (found.append(dict(r)) if r else unknown.append(c))
    return found, unknown


def _apply(conn, lines, promos):
    """Price lines with promos stacked additively per line (how the checkout actually applies them)."""
    subtotal = total = cost = 0.0
    detail = []
    for sku, qty in lines:
        p = dict(conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone())
        pct = sum(pr["pct_off"] for pr in promos if pr["applies_to"] in ("ALL", p["category"]))
        pct = min(pct, 0.9)
        unit = round(p["base_price"] * (1 - pct), 2)
        floor_unit = round(p["unit_cost"] / (1 - p["margin_floor_pct"]), 2)
        detail.append({
            "sku": sku, "qty": qty, "base_price": p["base_price"], "unit_cost": p["unit_cost"],
            "discount_pct": round(pct, 4), "unit_price": unit,
            "margin_floor_pct": p["margin_floor_pct"], "min_allowed_unit_price": floor_unit,
            "margin_pct": round((unit - p["unit_cost"]) / unit, 4) if unit else None,
            "below_floor": unit < floor_unit, "below_cost": unit < p["unit_cost"],
        })
        subtotal += p["base_price"] * qty
        total += unit * qty
        cost += p["unit_cost"] * qty
    return round(subtotal, 2), round(total, 2), round(cost, 2), detail


def _valid_combos(promos):
    """All promo combinations the rules allow: any set of stackable codes, or one non-stackable alone."""
    stackable = [p for p in promos if p["stackable"]]
    combos = [[]]
    for n in range(1, len(stackable) + 1):
        combos += [list(c) for c in combinations(stackable, n)]
    combos += [[p] for p in promos if not p["stackable"]]
    return combos


def price_lines(conn, lines: list[tuple[str, int]], codes: list[str]) -> dict:
    """Price an order two ways: as the checkout applied it, and as the promotion rules allow."""
    promos, unknown = _promos(conn, codes)
    subtotal, applied, cost, detail = _apply(conn, lines, promos)

    violations = []
    nonstack = [p["code"] for p in promos if not p["stackable"]]
    if nonstack and len(promos) > 1:
        violations.append({
            "rule": "non_stackable_combined",
            "message": f"{', '.join(nonstack)} cannot be combined with other codes, but {len(promos)} codes were applied",
            "codes": [p["code"] for p in promos],
        })
    if unknown:
        violations.append({"rule": "unknown_or_inactive_code", "codes": unknown})
    below = [d["sku"] for d in detail if d["below_floor"]]
    if below:
        violations.append({"rule": "below_margin_floor", "skus": below,
                           "message": "Unit price below the minimum allowed by the margin floor"})

    # Best price the customer is entitled to under the rules, never below the margin floor.
    best = None
    for combo in _valid_combos(promos):
        _, t, _, d = _apply(conn, lines, combo)
        floor_total = round(sum(x["min_allowed_unit_price"] * x["qty"] for x in d), 2)
        t_ok = max(t, floor_total)
        if best is None or t_ok < best["total"]:
            best = {"codes": [p["code"] for p in combo], "total": t_ok, "clamped_to_floor": t < floor_total}

    min_allowed = round(sum(d["min_allowed_unit_price"] * d["qty"] for d in detail), 2)
    return {
        "subtotal": subtotal,
        "price_as_applied": applied,
        "codes_applied": [p["code"] for p in promos],
        "total_cost": cost,
        "margin_pct_as_applied": round((applied - cost) / applied, 4) if applied else None,
        "min_allowed_total": min_allowed,
        "violations": violations,
        "price_by_rules": best["total"],
        "codes_by_rules": best["codes"],
        "price_by_rules_clamped_to_floor": best["clamped_to_floor"],
        "margin_pct_by_rules": round((best["total"] - cost) / best["total"], 4) if best["total"] else None,
        "lines": detail,
        "promotions": promos,
    }


# ───────────────────────── SLA / ETA ─────────────────────────

def stock_ready_at(conn, order_id: str):
    """When will the order's goods be in the warehouse? (now, or the latest open PO arrival)."""
    po = conn.execute(
        "SELECT * FROM purchase_orders WHERE for_order_id=? AND status IN ('open','delayed','shipped')"
        " ORDER BY expected_arrival DESC LIMIT 1", (order_id,)).fetchone()
    if po:
        return max(parse(po["expected_arrival"]), utcnow()), dict(po)
    return utcnow(), None


def sla_view(conn, order_id: str) -> dict | None:
    o = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        return None
    now = utcnow()
    promised = parse(o["promised_date"])
    ready, po = stock_ready_at(conn, order_id)
    options = []
    for c in conn.execute("SELECT * FROM carriers ORDER BY transit_days DESC"):
        eta = ready + timedelta(days=HANDLING_DAYS + c["transit_days"])
        options.append({
            "carrier": c["id"], "name": c["name"], "transit_days": c["transit_days"],
            "extra_cost": c["base_cost"], "eta": iso(eta), "meets_promise": eta <= promised,
            "slack_hours": round((promised - eta).total_seconds() / 3600, 1),
        })
    current = next(x for x in options if x["carrier"] == o["carrier"])
    shipped = o["status"] in ("shipped", "delivered")
    return {
        "order_id": order_id,
        "status": o["status"],
        "promised_date": o["promised_date"],
        "hours_left": round((promised - now).total_seconds() / 3600, 1),
        "current_carrier": o["carrier"],
        "stock_ready_at": iso(ready),
        "blocking_po": po,
        "current_eta": current["eta"],
        "at_risk": (not shipped) and not current["meets_promise"],
        "carrier_options": options,
    }
