# Architecture

## Component diagram

```
 ┌─────────────────────────── Mock systems (services/) ───────────────────────────┐
 │  Orders API   Inventory API   Supplier API   Payment API   Pricing API   SLA   │
 │      one FastAPI app on :8000 (ADR-005) · failures via /admin/trigger?fail=     │
 └───────────────┬────────────────────────────────────────────────▲──────────────┘
                 │ events / polling                                │ tool calls
                 ▼                                                 │
        ┌─────────────────┐      exception      ┌─────────────────┴─────────────┐
        │ Detector        │ ──────────────────► │ Agent (agent/)                │
        │ rules + monitors│                     │  investigate → propose options│
        └─────────────────┘                     │  tools · LLM · memory         │
                                                └───────────┬───────────────────┘
                                                            ▼
                                                ┌───────────────────────┐
                                                │ Policy engine         │
                                                │ auto-execute or       │
                                                │ escalate to human     │
                                                └─────┬───────────┬─────┘
                                           auto-fix   │           │ needs approval
                                                      ▼           ▼
                                                ┌───────────────────────┐
                                                │ SQLite: exceptions,   │◄──── Dashboard (dashboard/)
                                                │ trail, actions, memory│      queue · trail · approve
                                                └───────────────────────┘
```

## Data model (SQLite — `data/schema.sql`, file `data/oea.db`)

**Source systems (mocked)**
- `orders` (id, customer_id, status, warehouse_id, promo_codes[json], subtotal, discount, total, carrier, created_at, promised_date) · `order_items` · `order_status_history`
- `customers` (id, name, email, tier[VIP|Gold|Standard], city)
- `products` (sku, name, category, base_price, unit_cost, margin_floor_pct)
- `warehouses` (id, name, city, transfer_cost_per_unit) · `inventory` (sku, warehouse_id, on_hand, reserved, last_scan_qty, last_scan_at)
- `suppliers` · `supplier_catalog` (supplier_id, sku, unit_cost, lead_time_days, available_qty) · `purchase_orders` (id, sku, supplier_id, qty, unit_cost, for_order_id, promised_ship_date, expected_arrival, status, delay_reason)
- `payment_methods` (order_id, method, card_expiry, billing_country, ip_country) · `payment_attempts` (order_id, attempt_no, amount, status, decline_code, fraud_score, ts)
- `promotions` (code, pct_off, stackable, applies_to) · `carriers` (id, transit_days, base_cost)

**Agent / dashboard**
- `exceptions` (id, order_id, domain, severity, status, symptom, root_cause, confidence, created_at, resolved_at)
  - status: open | investigating | awaiting_approval | auto_resolved | resolved | rejected | failed
- `investigation_trail` (id, exception_id, step_no, type[thought|tool_call|tool_result|decision], content, ts)
- `proposed_actions` (id, exception_id, rank, action_type, params_json, cost, risk, rationale, requires_approval, status)
- `resolutions` (exception_id, action_id, outcome, human_feedback, resolved_by, minutes_to_resolve)
- `action_log` (every action endpoint call — NFR-3) · `notifications` (FR-14)

Seed: 50 orders, 20 customers, 3 warehouses, 4 suppliers, 12 resolved past exceptions (for FR-8 and metrics). Deterministic, timestamps relative to "now".

## Agent loop

1. Receive exception (order id + detected symptom).
2. System prompt = role + policy summary + available tools + output schema.
3. Loop (max ~8 tool calls): think → call tool → record to trail.
4. Emit structured JSON: `{root_cause, confidence, evidence[], options[{action, params, cost, risk, rationale}]}`.
5. Policy engine evaluates options → auto-execute top option or mark `awaiting_approval`.

## Tool contracts → mock endpoints (base `http://localhost:8000`, live docs at `/docs`)

| Tool | Endpoint | Key output |
|---|---|---|
| `get_order` | `GET /orders/{order_id}` | order, customer (tier), items, status_history, purchase_orders |
| `check_inventory` | `GET /inventory/{sku}?location=` | per warehouse: on_hand, reserved, last_scan_qty, `verified_available`, `scan_mismatch`, transfer cost |
| `get_supplier_status` | `GET /suppliers/status?po_id=` \| `?order_id=` \| `?sku=` | POs with status, delay_reason, days_until_arrival, `alternates[]` (lead time, `extra_cost_vs_current`) |
| `get_payment_status` | `GET /payments/{order_id}` | method, attempts (decline_code, fraud_score), `risk_signals` (card_expired, ip_billing_country_mismatch, max_fraud_score, decline_count, declines_within_minutes) |
| `get_pricing_rules` | `GET /pricing/{sku}?promo_codes=A,B&qty=` · `GET /pricing/order/{order_id}` | price_as_applied, `violations[]`, min_allowed_total, `price_by_rules` + codes_by_rules, margins |
| `get_sla` | `GET /sla/{order_id}` | promised_date, hours_left, current_eta, `at_risk`, blocking_po, carrier_options (eta, extra_cost, meets_promise) |
| `find_similar_cases` | `GET /cases/similar?domain=&symptom=&limit=3` | past cases with root_cause, action, outcome, resolved_by, similarity |
| `reallocate_stock` | `POST /actions/reallocate_stock` `{order_id, from, to}` | cost = units × target transfer cost, new_eta, meets_promise |
| `switch_supplier` | `POST /actions/switch_supplier` `{po_id, supplier_id}` | cost = unit-cost delta × qty, new_po_id, order_eta, meets_promise |
| `retry_payment` | `POST /actions/retry_payment` `{order_id}` | captured, or declined again (expired card / fraud) |
| `hold_order` | `POST /actions/hold_order` `{order_id, reason}` | previous_status · undo: `POST /actions/release_order` |
| `apply_price_correction` | `POST /actions/apply_price_correction` `{order_id, price, promo_codes?}` | old/new total, revenue_recovered; refuses below margin floor |
| `expedite_shipping` | `POST /actions/expedite_shipping` `{order_id, carrier}` | cost, new_eta, meets_promise |
| `notify_customer` | `POST /actions/notify_customer` `{order_id, message, channel?}` | notification_id |

All actions return `{ok, action, order_id, cost, message, …}`. A business-rule failure is HTTP 200 with `ok:false` (e.g. not enough verified stock); bad input is 4xx. Actions enforce physical reality, **not** approval policy — that's the policy engine.

Every read endpoint accepts `?fail=timeout|error|slow` (504 / 503 / +3 s) for testing agent error handling and the NFR-5 fallback.

## Demo control (FR-18)

| Endpoint | Purpose |
|---|---|
| `GET /admin/scenarios` | list scenarios with summary, detect hint, expected agent behaviour |
| `POST /admin/trigger?fail=<name>` | break a demo order by changing source data |
| `POST /admin/reset` | rebuild the clean seeded world (also wipes exceptions/trail) |
| `GET /admin/action_log` | audit log of executed actions |
| `GET /health` | liveness |

| `fail=` | Order | What changes | Detector should look for |
|---|---|---|---|
| `sc1` | ORD-1001 | WH-A scan finds 0 headphones (system says 5); order → `pick_failed` | `orders.status='pick_failed'` |
| `sc2` | ORD-1002 | PO-2001 (SUP-1) → `delayed`, arrives +7 d; order promised +6 d | PO `delayed` on an open order, or `/sla` `at_risk` |
| `sc3_expired` | ORD-1003 | 1 decline `expired_card`, fraud 0.04; order → `payment_failed` | `orders.status='payment_failed'` |
| `sc3_fraud` | ORD-1003 | 3 declines in 4 min, fraud ≤0.91, IP ≠ billing country | `orders.status='payment_failed'` |
| `sc4` | ORD-1004 | FEST20 + non-stackable NEWUSER15 stacked → ₹12,349 vs cost ₹13,000 | `/pricing/order/{id}` has `violations` |

Expected resolutions: SC-1 reallocate WH-A→WH-B (₹120, auto) · SC-2 switch to SUP-3 (+₹2,000) **and** EXPRESS (₹350) — switching alone is still late → escalate · SC-3a notify customer to update card (retry would fail) · SC-3b hold + escalate · SC-4 hold + price correction to ₹15,199.20 (FEST20 only) → human.

## Policy example (`agent/policy.json`)

```json
{
  "auto_execute": {
    "reallocate_stock": { "max_extra_cost": 200 },
    "retry_payment":    { "max_attempts": 2, "fraud_score_below": 0.3 },
    "expedite_shipping":{ "max_cost": 500 }
  },
  "always_escalate": ["switch_supplier", "apply_price_correction", "refund"],
  "escalate_if": { "customer_tier": ["VIP"], "order_value_above": 50000, "confidence_below": 0.7 }
}
```
