"""Rule-based investigator (owner: P2, NFR-5).

Used when the LLM is unavailable, errors, or returns something invalid — and as OEA_AGENT_MODE=rules
for a no-API-key demo. Same tools, same trail, same output shape as the LLM, so nothing downstream
can tell the difference except the trail's `source: "rules"`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.store import Trail
from agent.tools import Services, ToolError

HANDLING_DAYS = 0.5


class Investigator:
    def __init__(self, svc: Services, trail: Trail):
        self.svc, self.trail = svc, trail
        self.evidence: list[dict] = []

    def call(self, tool: str, **args):
        from agent.llm import _summary
        self.trail.tool_call(tool, args)
        try:
            data = self.svc.call(tool, args)
        except ToolError as e:
            self.trail.tool_result(tool, error=str(e))
            raise
        self.trail.tool_result(tool, data=data, summary=_summary(tool, data))
        return data

    def think(self, text: str):
        self.trail.thought(text, source="rules")

    def fact(self, source: str, fact: str):
        self.evidence.append({"source": source, "fact": fact})


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def run(exc: dict, svc: Services, trail: Trail) -> dict:
    inv = Investigator(svc, trail)
    inv.think(f"Running rule-based playbook for the {exc['domain']} exception on {exc['order_id']}.")
    order = inv.call("get_order", order_id=exc["order_id"])
    playbook = {"inventory": _inventory, "supplier": _supplier, "deadline": _supplier,
                "payment": _payment, "pricing": _pricing}.get(exc["domain"], _unknown)
    res = playbook(inv, exc, order)
    try:
        sim = inv.call("find_similar_cases", domain=exc["domain"], symptom=exc.get("symptom") or res["root_cause"])
        if sim["cases"]:
            top = sim["cases"][0]
            inv.fact("find_similar_cases",
                     f"Similar past case #{top['exception_id']}: '{top['root_cause']}' fixed by {top['action_type']} ({top['outcome']}).")
    except ToolError:
        pass
    res["evidence"] = inv.evidence
    return res


# ───────── inventory ─────────

def _inventory(inv: Investigator, exc: dict, order: dict) -> dict:
    src = order.get("warehouse_id")
    for item in order["items"]:
        stock = inv.call("check_inventory", sku=item["sku"])
        locs = {l["warehouse_id"]: l for l in stock["locations"]}
        s = locs.get(src)
        if s and (s["scan_mismatch"] or s["last_scan_qty"] < item["qty"]):
            inv.fact("check_inventory",
                     f"{src} system shows {s['on_hand']} × {item['sku']} but last physical scan found {s['last_scan_qty']}.")
            cands = sorted([l for w, l in locs.items() if w != src and l["verified_available"] >= item["qty"]],
                           key=lambda l: l["transfer_cost_per_unit"])
            if cands:
                best = cands[0]
                inv.fact("check_inventory",
                         f"{best['warehouse_id']} ({best['city']}) has {best['verified_available']} verified available, scan matches system.")
                inv.think(f"Phantom stock at {src}. {best['warehouse_id']} can cover {item['qty']} unit(s).")
                sla = inv.call("get_sla", order_id=order["id"])
                cost = item["qty"] * best["transfer_cost_per_unit"]
                move = {"type": "reallocate_stock", "params": {"order_id": order["id"], "from": src, "to": best["warehouse_id"]}}
                promised = sla["promised_date"][:10]
                if sla["at_risk"]:
                    # Second problem (SC-5): even with stock found, the current carrier misses the promise.
                    inv.fact("get_sla", f"With {sla['current_carrier'].lower()} shipping the order arrives {sla['current_eta'][:10]}, "
                                        f"after the promised {promised} ({sla['hours_left']:.0f} h left).")
                    fast = sorted([c for c in sla["carrier_options"] if c["meets_promise"]], key=lambda c: c["extra_cost"])
                    if fast:
                        inv.think(f"Two problems: phantom stock and a tight deadline. Move the stock to {best['warehouse_id']} "
                                  f"and upgrade to {fast[0]['name']}, the cheapest carrier that still meets the promise.")
                        options = [{
                            "title": f"Ship from {best['warehouse_name']} by {c['name']}",
                            "steps": [move, {"type": "expedite_shipping", "params": {"order_id": order["id"], "carrier": c["carrier"]}}],
                            "cost": cost + c["extra_cost"], "risk": "low",
                            "impact": f"Order continues; arrives {c['eta'][:10]}, promise of {promised} met",
                            "rationale": f"Verified stock in {best['warehouse_id']} (₹{cost:,.0f}) plus {c['name']} "
                                         f"(₹{c['extra_cost']:,.0f}); slower carriers would be late.",
                        } for c in fast[:2]]
                        return {"root_cause": f"Phantom stock: {src} system count ({s['on_hand']}) for {item['sku']} doesn't match the "
                                              f"physical scan ({s['last_scan_qty']}), and with {sla['hours_left']:.0f} h left on the promise "
                                              f"standard shipping would be late, so both need fixing.",
                                "confidence": 0.9, "options": options, "containment": []}
                    inv.think("No carrier can meet the promise any more; the customer has to be told.")
                    options = [{"title": f"Ship from {best['warehouse_name']} and tell the customer it's late",
                                "steps": [move, {"type": "notify_customer", "params": {"order_id": order["id"],
                                          "message": f"Sorry, your order will arrive after {promised}. It ships today from our "
                                                     f"{best['city']} warehouse."}}],
                                "cost": cost, "risk": "medium", "impact": "Order continues but misses the promise",
                                "rationale": "Stock found, but no carrier is fast enough."}]
                    return {"root_cause": f"Phantom stock at {src} and too little time left for any carrier to meet the promise.",
                            "confidence": 0.85, "options": options, "containment": []}
                inv.fact("get_sla", f"Standard shipping still arrives by {sla['current_eta'][:10]}, within the promised {promised}.")
                options = [{
                    "title": f"Ship from {best['warehouse_name']}",
                    "steps": [move],
                    "cost": cost, "risk": "low",
                    "impact": f"Order continues; delivery by {promised} still met",
                    "rationale": f"Verified stock in {best['warehouse_id']}; extra cost ₹{cost:,.0f}.",
                }]
                for alt in cands[1:2]:
                    c2 = item["qty"] * alt["transfer_cost_per_unit"]
                    options.append({
                        "title": f"Ship from {alt['warehouse_name']}",
                        "steps": [{"type": "reallocate_stock",
                                   "params": {"order_id": order["id"], "from": src, "to": alt["warehouse_id"]}}],
                        "cost": c2, "risk": "low", "impact": "Order continues", "rationale": "Second-cheapest verified source.",
                    })
                return {"root_cause": f"Phantom stock: {src} system count ({s['on_hand']}) for {item['sku']} doesn't match the "
                                      f"physical scan ({s['last_scan_qty']}), so the pick failed.",
                        "confidence": 0.92, "options": options, "containment": []}
            inv.think(f"No other warehouse holds verified stock of {item['sku']}.")
            return {"root_cause": f"Phantom stock at {src} and no verified stock elsewhere for {item['sku']}.",
                    "confidence": 0.8, "containment": [],
                    "options": [{"title": "Hold and tell the customer", "risk": "medium", "cost": 0,
                                 "steps": [{"type": "hold_order", "params": {"order_id": order["id"], "reason": "no_stock"}},
                                           {"type": "notify_customer", "params": {"order_id": order["id"],
                                            "message": "Your order is delayed while we restock. We'll update you within 24 hours."}}],
                                 "rationale": "No reallocation possible; needs replenishment."}]}
    return _unknown(inv, exc, order)


# ───────── supplier / deadline ─────────

def _supplier(inv: Investigator, exc: dict, order: dict) -> dict:
    oid = order["id"]
    sla = inv.call("get_sla", order_id=oid)
    promised = _dt(sla["promised_date"])
    sup = inv.call("get_supplier_status", order_id=oid)
    po = next((p for p in sup["purchase_orders"] if p["status"] in ("delayed", "open", "shipped")), None)
    carriers = sla["carrier_options"]
    options: list[dict] = []
    if po and po["is_delayed"]:
        inv.fact("get_supplier_status", f"{po['id']} from {po['supplier']['name']} is delayed: {po['delay_reason']}. "
                                        f"Arrives in {po['days_until_arrival']} days.")
        inv.fact("get_sla", f"Promised {sla['promised_date'][:10]}; current ETA {sla['current_eta'][:10]} — "
                            f"{'misses' if sla['at_risk'] else 'meets'} the promise. No carrier upgrade alone fixes it."
                 if not any(c["meets_promise"] for c in carriers) else
                 f"Promised {sla['promised_date'][:10]}; current ETA {sla['current_eta'][:10]}.")
        now = datetime.now(timezone.utc)
        for alt in po["alternates"]:
            ready = now + timedelta(days=alt["lead_time_days"])
            for c in sorted(carriers, key=lambda c: c["extra_cost"]):
                eta = ready + timedelta(days=HANDLING_DAYS + c["transit_days"])
                if eta <= promised:
                    steps = [{"type": "switch_supplier", "params": {"po_id": po["id"], "supplier_id": alt["supplier_id"]}}]
                    if c["carrier"] != sla["current_carrier"]:
                        steps.append({"type": "expedite_shipping", "params": {"order_id": oid, "carrier": c["carrier"]}})
                    cost = alt["extra_cost_vs_current"] + c["extra_cost"]
                    options.append({
                        "title": f"Switch to {alt['name']}" + (f" + {c['carrier']}" if len(steps) > 1 else ""),
                        "steps": steps, "cost": cost, "risk": "medium",
                        "impact": f"Delivered by {eta.date()} (promise {promised.date()})",
                        "rationale": f"{alt['name']} ships in {alt['lead_time_days']} d (reliability {alt['reliability']:.0%}); "
                                     f"{c['carrier']} gets it there in time. Extra ₹{cost:,.0f}.",
                    })
                    break
        options.sort(key=lambda o: o["cost"])
        options = options[:2]
        late = _dt(sla["current_eta"]).date()
        options.append({
            "title": "Keep supplier, tell customer about the delay",
            "steps": [{"type": "notify_customer", "params": {"order_id": oid, "message":
                       f"Your standing desk is delayed at our supplier. New expected delivery: {late}. "
                       f"Reply to this email if you'd like to cancel for a full refund."}}],
            "cost": 0, "risk": "high", "impact": f"Delivery slips to {late}; SLA breached, churn risk",
            "rationale": "No extra cost, but breaks the promise to the customer.",
        })
        if options[0]["steps"][0]["type"] != "switch_supplier":
            inv.think("No alternate supplier can meet the promised date.")
        else:
            inv.think(f"{len(options) - 1} way(s) to still meet the promise; cheapest costs ₹{options[0]['cost']:,.0f}.")
        return {"root_cause": f"{po['supplier']['name']} delayed {po['id']} ({po['delay_reason']}), pushing delivery past the promised date.",
                "confidence": 0.9, "containment": [], "options": options,
                "customer_message": options[-1]["steps"][0]["params"]["message"]}
    if sla["at_risk"]:
        fix = next((c for c in sorted(carriers, key=lambda c: c["extra_cost"]) if c["meets_promise"]), None)
        if fix:
            inv.fact("get_sla", f"Current ETA {sla['current_eta'][:10]} misses promise {sla['promised_date'][:10]}; {fix['carrier']} meets it.")
            return {"root_cause": "Fulfilment is running late on the current carrier.", "confidence": 0.8, "containment": [],
                    "options": [{"title": f"Upgrade to {fix['carrier']}", "cost": fix["extra_cost"], "risk": "low",
                                 "steps": [{"type": "expedite_shipping", "params": {"order_id": oid, "carrier": fix["carrier"]}}],
                                 "impact": f"Arrives by {fix['eta'][:10]}", "rationale": "Cheapest carrier that meets the promise."}]}
    return _unknown(inv, exc, order)


# ───────── payment ─────────

def _payment(inv: Investigator, exc: dict, order: dict) -> dict:
    oid = order["id"]
    pay = inv.call("get_payment_status", order_id=oid)
    s = pay["risk_signals"]
    last = pay["attempts"][-1] if pay["attempts"] else {}
    fraudy = s.get("max_fraud_score", 0) >= 0.5 or s.get("ip_billing_country_mismatch") or last.get("decline_code") == "suspected_fraud"
    if fraudy:
        inv.fact("get_payment_status", f"{s.get('decline_count', 0)} declines within {s.get('declines_within_minutes', 0)} min; "
                                       f"fraud score up to {s.get('max_fraud_score')}.")
        if s.get("ip_billing_country_mismatch"):
            inv.fact("get_payment_status", f"Checkout IP country {pay['method']['ip_country']} ≠ billing country {pay['method']['billing_country']}.")
        inv.think("Pattern matches card testing / account takeover. Do not retry; hold and send to risk review.")
        return {"root_cause": "Likely fraudulent payment attempt: rapid repeated declines, high gateway fraud score and "
                              "IP/billing country mismatch.",
                "confidence": 0.88,
                "containment": [{"type": "hold_order", "params": {"order_id": oid, "reason": "fraud_review"}}],
                "options": [
                    {"title": "Keep on hold, ask customer to verify", "risk": "medium", "cost": 0,
                     "steps": [{"type": "notify_customer", "params": {"order_id": oid, "message":
                                "We couldn't confirm your payment. Please verify your identity using the link in this email to continue your order."}}],
                     "impact": "Order waits for verification; no charge", "rationale": "Protects the customer if the account is compromised."},
                    {"title": "Release and retry payment", "risk": "high", "cost": 0,
                     "steps": [{"type": "release_order", "params": {"order_id": oid}},
                               {"type": "retry_payment", "params": {"order_id": oid}}],
                     "impact": "Only if risk team confirms the customer", "rationale": "For a confirmed false positive."},
                ]}
    if s.get("card_expired"):
        inv.fact("get_payment_status", f"Card ending {pay['method']['card_last4']} expired {pay['method']['card_expiry']}; "
                                       f"decline code {last.get('decline_code')}; fraud score {s.get('max_fraud_score')}.")
        inv.think("Card is expired and risk is low — a retry would fail. Ask the customer for a new payment method.")
        msg = ("The card on your order has expired. Use this secure link to add a new payment method and we'll ship "
               "your order right away.")
        return {"root_cause": "The customer's card has expired; the issuer declined the authorisation.",
                "confidence": 0.95, "containment": [], "customer_message": msg,
                "options": [
                    {"title": "Send payment-update link", "risk": "low", "cost": 0,
                     "steps": [{"type": "notify_customer", "params": {"order_id": oid, "message": msg}}],
                     "impact": "Customer updates card; order resumes", "rationale": "Retry can't succeed on an expired card."},
                    {"title": "Hold order until customer responds", "risk": "low", "cost": 0,
                     "steps": [{"type": "hold_order", "params": {"order_id": oid, "reason": "awaiting_payment_update"}}],
                     "impact": "Stock stays reserved", "rationale": "Avoids auto-cancellation."},
                ]}
    inv.fact("get_payment_status", f"Last decline code {last.get('decline_code')}; low fraud score {s.get('max_fraud_score')}.")
    return {"root_cause": f"Transient payment failure ({last.get('decline_code') or 'unknown'}).", "confidence": 0.75,
            "containment": [],
            "options": [{"title": "Retry payment", "risk": "low", "cost": 0,
                         "steps": [{"type": "retry_payment", "params": {"order_id": oid}}],
                         "impact": "Order proceeds if capture succeeds", "rationale": "Low risk, transient failure."}]}


# ───────── pricing ─────────

def _pricing(inv: Investigator, exc: dict, order: dict) -> dict:
    oid = order["id"]
    p = inv.call("get_pricing_rules", order_id=oid)
    if not p["violations"]:
        return _unknown(inv, exc, order)
    rules = {v["rule"]: v for v in p["violations"]}
    charged = p["charged_total"]
    inv.fact("get_pricing_rules", f"Charged ₹{charged:,.2f} with codes {', '.join(p['codes_applied'])}; cost ₹{p['total_cost']:,.2f}; "
                                  f"margin floor needs ≥ ₹{p['min_allowed_total']:,.2f}.")
    if "non_stackable_combined" in rules:
        inv.fact("get_pricing_rules", rules["non_stackable_combined"]["message"])
        cause = (f"Checkout stacked {' + '.join(rules['non_stackable_combined']['codes'])} although "
                 f"non-stackable codes can't be combined, selling below margin floor.")
    else:
        cause = "Discounts push the order below the margin floor."
    correct = p["price_by_rules"]
    inv.think(f"Correct price under the promo rules is ₹{correct:,.2f} ({', '.join(p['codes_by_rules']) or 'no code'}). "
              f"Hold the order so it doesn't ship at a loss.")
    loss = round(p["total_cost"] - charged, 2)
    msg = (f"We made a pricing error on your order: two offers were combined that can't be used together. "
           f"The correct price with your best offer is ₹{correct:,.2f}. Reply YES to confirm or we'll cancel with a full refund.")
    return {"root_cause": cause, "confidence": 0.93, "customer_message": msg,
            "containment": [{"type": "hold_order", "params": {"order_id": oid, "reason": "pricing_review"}}],
            "options": [
                {"title": f"Correct price to ₹{correct:,.2f} and ask customer", "risk": "medium", "cost": 0,
                 "steps": [{"type": "apply_price_correction", "params": {"order_id": oid, "price": correct,
                                                                        "promo_codes": p["codes_by_rules"]}},
                           {"type": "notify_customer", "params": {"order_id": oid, "message": msg}}],
                 "impact": f"Recovers ₹{correct - charged:,.2f}; customer must accept",
                 "rationale": "Applies the best valid offer; respects margin floor."},
                {"title": "Honour the price, absorb the loss", "risk": "high", "cost": max(loss, 0),
                 "steps": [{"type": "release_order", "params": {"order_id": oid}}],
                 "impact": f"Ships at ₹{charged:,.2f}; loss ₹{max(loss, 0):,.2f}", "rationale": "Goodwill only — sells below cost."},
            ]}


def _unknown(inv: Investigator, exc: dict, order: dict) -> dict:
    inv.think("No playbook matched the data. Holding for a human.")
    return {"root_cause": "Undetermined — rule playbook found no matching pattern.", "confidence": 0.3, "containment": [],
            "options": [{"title": "Hold for manual review", "risk": "low", "cost": 0,
                         "steps": [{"type": "hold_order", "params": {"order_id": order["id"], "reason": "manual_review"}}],
                         "rationale": "Agent could not determine the cause."}]}
