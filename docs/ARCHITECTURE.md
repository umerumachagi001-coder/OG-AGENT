# Architecture

## Component diagram (ADR-009: n8n runs the process)

```
                      ┌──────────────────────── n8n  :5678 ─────────────────────────┐
                      │ OEA 1 · Detect exceptions      every 10 s + webhook oea-scan │
                      │   Run detector rules → Start investigation (per exception)   │
                      │ OEA 2 · Investigate and resolve                              │
                      │   Claim → Which systems to check? → Check stock / supplier / │
                      │   payment / pricing → Past cases → Ask Claude (or Rule       │
                      │   playbook) → Apply approval policy (Code) → Hold order →    │
                      │   Fix automatically │ Ping Slack → Wait for a person → Run   │
                      └────────┬───────────────────────────────▲──────────┬─────────┘
               /n8n/* bridge   │                     resume URL │          │ api.anthropic.com
                               ▼                               │          ▼
 Dashboard :8001/ui ──► Orchestrator :8001 (orchestrator/app.py + n8n_bridge.py) ── Claude
   Approve / Reject ────────┘   │ records trail, verifies costs, executes actions
                                ▼
            Mock company systems :8000 (services/) — orders, stock, suppliers, payments,
            pricing, delivery; failures via /admin/trigger?fail=        SQLite: data/oea.db
```

`OEA_ENGINE=python` swaps n8n for the built-in Python loop (`agent/runner.py`), same steps and results.

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
- `exceptions` (id, order_id, domain, severity, status, symptom, root_cause, confidence, created_at, resolved_at, origin[live|history])
  - status: open | investigating | awaiting_approval | auto_resolved | resolved | rejected | failed
- `investigation_trail` (id, exception_id, step_no, type[thought|tool_call|tool_result|decision], content, ts)
- `proposed_actions` (id, exception_id, rank, action_type, params_json, cost, risk, rationale, requires_approval, status)
- `resolutions` (exception_id, action_id, outcome, human_feedback, resolved_by, minutes_to_resolve)
- `action_log` (every action endpoint call — NFR-3) · `notifications` (FR-14)

Seed: 50 orders, 20 customers, 3 warehouses, 4 suppliers, 12 resolved past exceptions (for FR-8 and metrics). Deterministic, timestamps relative to "now".

## n8n workflows and bridge (`n8n/`, `orchestrator/n8n_bridge.py`)

Full step list, settings and tests: `n8n/README.md`. The bridge endpoints, in the order workflow 2 calls them:

| Endpoint | Purpose |
|---|---|
| `POST /n8n/scan` | run detector rules → `{created: [...], count}` |
| `POST /n8n/exceptions/{id}/start` | claim (→ investigating); returns order SKUs, tier; `claimed:false` if already handled |
| `POST /n8n/exceptions/{id}/tool` `{tool, input}` | one read-only check against the systems, logged to the trail |
| `POST /n8n/exceptions/{id}/llm-request` | `{enabled, request}`: the Anthropic Messages body built from the gathered evidence |
| `POST /n8n/exceptions/{id}/playbook?reason=` | rule-based diagnosis (Claude off / failed) |
| `POST /n8n/exceptions/{id}/prepare` `{resolution, source}` | validate, record diagnosis, verify costs → `{valid, context, options, containment, policy}` |
| `POST /n8n/exceptions/{id}/step` `{step, kind}` | containment step (hold the order) |
| `POST /n8n/exceptions/{id}/decide` `{options, verdicts, context, resume_url}` | store options + n8n's policy verdicts → `{auto, action_id}` or escalate (`notify_text`) |
| `POST /n8n/actions/{id}/run` `{by, steps?}` | execute: `by=agent` (auto) or a person after approval |
| `POST /n8n/exceptions/{id}/failed` | n8n error path → case shown as "agent stopped" |

Approve/Reject in the dashboard: the orchestrator POSTs `{verdict, action_id, by, steps}` to the saved resume URL
(table `n8n_waits`); n8n continues from **Wait for a person**. If n8n can't be reached, the orchestrator executes
the approval itself. Reset closes any paused runs first.

## Detector (`services/detector/rules.py`, FR-1..4)

Called by n8n every 10 s and on `/trigger` (or every 2 s by the Python loop when `OEA_ENGINE=python`). One exception per (order, domain);
skipped while one is open/awaiting/rejected or was resolved < 5 min ago.

| Rule | Fires when | Domain |
|---|---|---|
| pick_failed | order status `pick_failed` | inventory |
| payment_failed | order status `payment_failed` | payment |
| supplier_delay | open order waits on a `delayed` PO | supplier |
| sla_at_risk | ETA misses the promise (predicted, FR-4) | deadline |
| margin_breach | unshipped order breaks promo/margin rules | pricing |
| stalled | no status change for 48 h | deadline |

Severity points: 1 base (+1 if SLA at risk / sold below cost) · value > ₹15k +1, > ₹50k +2 · Gold +1, VIP +2 ·
< 96 h left +1, < 48 h +2 → ≥6 critical, ≥4 high, ≥2 medium, else low.

## Orchestrator API (`orchestrator/app.py`, port 8001) — what the dashboard (P3) calls

| Endpoint | Returns / does |
|---|---|
| `GET /exceptions?status=` | live queue (history hidden; `include_history=true` to show), open work first, then severity, then newest. Each row has order_value, customer_name/tier, pending_approvals |
| `GET /exceptions/{id}` | exception + `order` + `actions[]` (with `params.steps`, `params.policy.reasons`) + `resolution` + `evidence[]` + `customer_message` + `investigating` |
| `GET /exceptions/{id}/trail?after=N` | trail steps after N — poll every ~1 s for the live view |
| `POST /exceptions/{id}/investigate` | 202; re-run agent (status must be open/failed) |
| `POST /actions/{id}/approve` `{by, steps?}` | execute option (modified `steps` allowed) → exception `resolved` |
| `POST /actions/{id}/reject` `{by, feedback}` | reject; exception → `rejected` when no options remain |
| `GET /metrics` | exceptions_total, open, awaiting_approval, resolved, auto_resolved, auto_resolved_pct, avg_minutes_auto/human, analyst_minutes_saved (20 min/case baseline), revenue_protected, action_cost_spent |
| `GET /scenarios` · `POST /trigger?fail=` · `POST /reset` · `GET /orders` | demo controls, proxied to services; `/trigger` returns `exceptions_created` |
| `POST /detect` · `GET /health` | manual detector run · liveness + agent mode |

Background loop: detect → auto-investigate new exceptions in a worker pool (`OEA_AUTO_INVESTIGATE=1`).
Run everything: `make demo` (services :8000 + orchestrator :8001). Scenario walkthrough: `make demo-sc1` … `demo-sc4`.

## Dashboard (`dashboard/`, P3) — OrderGuard AI

React + Vite + Tailwind v4, fonts bundled (works offline), no extra libraries (hash routing and charts are hand-written).
Served built at `http://localhost:8001`. Reads the orchestrator (:8001) and, read-only, the mock systems (:8000) for
suppliers, purchase orders and the action log. Every figure comes from these APIs; nothing is simulated in the browser.

| Route | Screen | Data |
|---|---|---|
| `#/overview` | KPIs, **order network** (each dot = a real order on its fulfilment-stage ring, sized by value, problem orders labelled), pending decisions, priority queue, activity feed, exceptions by type | `/health`, `/orders`, `/exceptions`, `/metrics`, trails |
| `#/exceptions` | Search, status + type filters, sort | `/exceptions` |
| `#/case/{id}` | Investigation: detected symptom vs assessment, key facts in plain language (each cites its check), decision panel, timeline labelled Observed / Reasoning / Assessment / Policy / Person / Action ran, workflow progress, evidence, policy findings, actions executed, decision history, order record, raw JSON | `/exceptions/{id}`, `/trail` |
| `#/approvals` | Pending decisions with assessment + problem facts, approve (confirm dialog) / edit / reject (reason); decisions taken with approver + time | `/actions/{id}/approve`, `/reject` |
| `#/workflow` | 7-stage pipeline graph per case and stage table; states derived from the trail; n8n connection + editor link | trails, `/health` |
| `#/audit` | Case events + system action log, type filter, search, source data | trails, `:8000/admin/action_log` |
| `#/suppliers` | On-time rate dot plot (80–100% scale), risk bands (≥95 low, 90–95 moderate, <90 elevated), open POs, alternates, table | `:8000/suppliers`, `/suppliers/status` |
| `#/order/{id}` | Order without an exception (search target) | `:8000/orders/{id}` |

Approvals only report what the server confirms: `resolved` → done; n8n `resuming` → the UI watches the case until n8n has
run the fix (or failed); a decision already taken elsewhere is reported and refreshed. Duplicate sends are blocked.

Design tokens: night `#0A1324`, deck `#0F1B30`, raised `#15243E`, line `#22324E`, ink `#E7EDF6`, signal cyan `#3CCFD3`
(interface only), data blue `#5B9CF0`; status good `#2BB24C`, warning `#FAB219`, critical `#E5534B`, always with a text
label or shape. Barlow / Barlow Semi Condensed.

## Agent (agent/) — loop, output, entry points

**Loop** (`agent/llm.py`): system prompt = role + rules + action catalogue + policy → Claude tool loop over the 7
read-only tools (max `OEA_MAX_TOOL_CALLS`=8, then `submit_resolution` is forced) → every thought, tool call and
result written to the trail → output validated. Any API error, timeout or invalid output → rule-based playbooks
(`agent/fallback.py`, NFR-5) which use the same tools, trail and output shape. `OEA_AGENT_MODE=rules` runs them
without an API key.

**Output** (`submit_resolution`):
```json
{ "root_cause": "…", "confidence": 0.92,
  "evidence":    [{"source": "check_inventory", "fact": "WH-A system 5, scan 0"}],
  "containment": [{"type": "hold_order", "params": {"order_id": "…", "reason": "fraud_review"}}],
  "options": [{ "title": "Switch to OfficeWorks + EXPRESS", "cost": 2350, "risk": "medium",
                "impact": "Delivered by 2026-10-20", "rationale": "…",
                "steps": [{"type": "switch_supplier",   "params": {"po_id": "PO-2001", "supplier_id": "SUP-3"}},
                          {"type": "expedite_shipping", "params": {"order_id": "ORD-1002", "carrier": "EXPRESS"}}] }],
  "customer_message": "optional draft (FR-14)" }
```
An option is a **list of steps** (ADR-006). In `proposed_actions`: `action_type` = step types joined with `+`,
`params_json` = `{title, steps, impact, policy: {auto, reasons, checks}}`.

**Decide** (`agent/runner.py`): runner fetches tier / order value / fraud score itself and re-prices every step
from live data (the LLM's cost claims are not trusted; mismatches are logged as `cost_check`). Containment steps
in `containment_allowed` run immediately. Policy evaluates each option; if rank 1 is auto it executes →
`auto_resolved`, otherwise → `awaiting_approval`.

**Entry points for P4's orchestrator** (all return JSON-serialisable dicts):
| Function | Use for |
|---|---|
| `runner.investigate(exception_id)` | `POST /exceptions/{id}/investigate` |
| `runner.approve(action_id, by, steps=None)` | `POST /actions/{id}/approve` (pass `steps` to modify, FR-13) |
| `runner.reject(action_id, by, feedback)` | `POST /actions/{id}/reject` |
| `store.get_trail(exception_id)` / `store.get_actions(exception_id)` | dashboard reads |

Exception status: `open → investigating → auto_resolved | awaiting_approval → resolved | rejected`.

**Trail `content` shapes** (JSON object, for P3):
| type | content |
|---|---|
| thought | `{text, source: llm\|rules\|system}` |
| tool_call | `{tool, input}` |
| tool_result | `{tool, ok, summary, data, error}` — `summary` is a one-line human string |
| decision | `{kind, …}` kind ∈ diagnosis (root_cause, confidence, evidence, customer_message) · policy (context, options[]) · containment · cost_check · execution (steps, actual_cost) · escalation (reason, recommended) · fallback (reason) · human (verdict, by, feedback) |

CLI: `python -m agent dev ORD-1001 inventory "Pick failed"` · `investigate <id>` · `approve <action_id>` · `reject` · `trail <id>`.

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
| `sc5` | ORD-1001 | Placed 2 days earlier (promise now +3 d), then the SC-1 phantom stock; standard shipping (4 d + handling) is late | `pick_failed` **and** `/sla` `at_risk` → one inventory case, severity +1, symptom says the promise is also at risk |

Expected resolutions: SC-1 reallocate WH-A→WH-B (₹120, auto) · SC-2 switch to SUP-3 (+₹2,000) **and** EXPRESS (₹350) — switching alone is still late → escalate · SC-3a notify customer to update card (retry would fail) · SC-3b hold + escalate · SC-4 hold + price correction to ₹15,199.20 (FEST20 only) → human · SC-5 reallocate WH-A→WH-B **and** EXPRESS (₹470, auto; NEXT_DAY ₹1,020 offered as option 2, needs approval).

## Policy (`agent/policy.json`, FR-11)

```json
{
  "auto_execute": {
    "reallocate_stock":  { "max_extra_cost": 200 },
    "retry_payment":     { "max_attempts": 2, "fraud_score_below": 0.3 },
    "expedite_shipping": { "max_cost": 500 },
    "notify_customer":   {},
    "hold_order":        {}
  },
  "always_escalate": ["switch_supplier", "apply_price_correction", "release_order", "refund"],
  "escalate_if": { "customer_tier": ["VIP"], "order_value_above": 50000, "confidence_below": 0.7,
                   "fraud_score_above": 0.5, "option_risk": ["high"] },
  "max_option_cost": 1000,
  "containment_allowed": ["hold_order"]
}
```
Any step type not under `auto_execute` needs a human. Every reason is shown to the approver.

Reference outcomes (rules mode, tested): SC-1 auto-resolved (₹120) · SC-2 escalated, rank 1 switch+EXPRESS ₹2,350 ·
SC-3a auto-resolved by notify (no retry) · SC-3b held + escalated (fraud, severity high) · SC-4 held + escalated, correction to ₹15,199.20 ·
SC-5 auto-resolved with two steps (₹470).

**Undo (FR-15, ADR-011):** `POST /exceptions/{id}/undo` reverses an automatic fix newest step first via the services'
`POST /actions/undo {log_id}` and hands the case to a person. Reversible: reallocate stock, upgrade shipping, hold.
