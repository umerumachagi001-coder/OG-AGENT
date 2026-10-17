"""Tool registry (owner: P2, FR-5).

Two kinds of tools:
  INVESTIGATION tools — read-only, given to the LLM. Each maps to one mock endpoint.
  ACTION types        — never called by the LLM directly. The LLM *proposes* them inside
                        options; the policy engine decides, and `execute_step` runs them.

Contracts: docs/ARCHITECTURE.md → "Tool contracts → mock endpoints".
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from agent import config

# ───────────── Investigation tools (Claude tool schemas) ─────────────

INVESTIGATION_TOOLS: list[dict] = [
    {
        "name": "get_order",
        "description": "Order header, customer (incl. tier), line items, status history and any purchase orders tied to it. Start here.",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "check_inventory",
        "description": "Stock for a SKU per warehouse: system on_hand, reserved, last physical scan, verified_available "
                       "(trusts the lower of system vs scan), scan_mismatch flag, and per-unit cost of fulfilling from there.",
        "input_schema": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "location": {"type": "string", "description": "Optional warehouse id, e.g. WH-A"}}, "required": ["sku"]},
    },
    {
        "name": "get_supplier_status",
        "description": "Purchase orders with status, delay reason, days until arrival, and alternate suppliers "
                       "(lead time, extra cost vs current PO). Pass order_id, po_id or sku.",
        "input_schema": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "po_id": {"type": "string"}, "sku": {"type": "string"}}},
    },
    {
        "name": "get_payment_status",
        "description": "Payment method, every authorisation attempt (decline code, gateway fraud score) and derived "
                       "risk_signals: card_expired, ip_billing_country_mismatch, max_fraud_score, decline_count, declines_within_minutes.",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "get_pricing_rules",
        "description": "Price check. With order_id: re-prices the whole order. With sku (+ promo_codes, comma separated): "
                       "prices one SKU. Returns price_as_applied, rule violations, min_allowed_total (margin floor), "
                       "price_by_rules (correct price under promo rules) and codes_by_rules.",
        "input_schema": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "sku": {"type": "string"},
            "promo_codes": {"type": "string"}, "qty": {"type": "integer"}}},
    },
    {
        "name": "get_sla",
        "description": "Delivery promise check: promised_date, hours_left, current ETA, at_risk, the PO blocking stock "
                       "(if any) and each carrier option with ETA, extra cost and whether it meets the promise.",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
    {
        "name": "find_similar_cases",
        "description": "Past resolved exceptions in a domain ranked by symptom similarity, with root cause, action "
                       "taken, outcome and who resolved it. Use to reuse what worked.",
        "input_schema": {"type": "object", "properties": {
            "domain": {"type": "string", "enum": ["inventory", "supplier", "payment", "pricing", "deadline"]},
            "symptom": {"type": "string"}}, "required": ["domain"]},
    },
]

# ───────────── Action types the agent may propose ─────────────
# name -> (endpoint, required params, short description for the prompt)
ACTIONS: dict[str, tuple[str, list[str], str]] = {
    "reallocate_stock": ("/actions/reallocate_stock", ["order_id", "from", "to"],
                         "fulfil the order from another warehouse"),
    "switch_supplier": ("/actions/switch_supplier", ["po_id", "supplier_id"],
                        "cancel a PO and re-place it with an alternate supplier"),
    "retry_payment": ("/actions/retry_payment", ["order_id"], "re-attempt authorisation on the same method"),
    "hold_order": ("/actions/hold_order", ["order_id", "reason"], "stop the order from progressing"),
    "release_order": ("/actions/release_order", ["order_id"], "release a held order"),
    "apply_price_correction": ("/actions/apply_price_correction", ["order_id", "price"],
                               "re-price the order (optionally pass promo_codes the price is based on)"),
    "expedite_shipping": ("/actions/expedite_shipping", ["order_id", "carrier"],
                          "upgrade carrier: EXPRESS or NEXT_DAY"),
    "notify_customer": ("/actions/notify_customer", ["order_id", "message"], "email the customer"),
}


class ToolError(Exception):
    pass


class Services:
    """Thin HTTP client for the mock systems. `http` can be any object with httpx-style get/post
    (tests pass FastAPI's TestClient)."""

    def __init__(self, http: Any | None = None):
        self.http = http or httpx.Client(base_url=config.SERVICES_URL, timeout=config.HTTP_TIMEOUT_S)

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            r = self.http.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        except httpx.HTTPError as e:
            raise ToolError(f"{path}: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise ToolError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self.http.post(path, json=body)
        except httpx.HTTPError as e:
            raise ToolError(f"{path}: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise ToolError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    # investigation
    def call(self, name: str, args: dict) -> dict:
        a = args or {}
        if name == "get_order":
            return self._get(f"/orders/{a['order_id']}")
        if name == "check_inventory":
            return self._get(f"/inventory/{a['sku']}", {"location": a.get("location")})
        if name == "get_supplier_status":
            return self._get("/suppliers/status", {k: a.get(k) for k in ("order_id", "po_id", "sku")})
        if name == "get_payment_status":
            return self._get(f"/payments/{a['order_id']}")
        if name == "get_pricing_rules":
            if a.get("order_id"):
                return self._get(f"/pricing/order/{a['order_id']}")
            return self._get(f"/pricing/{a['sku']}", {"promo_codes": a.get("promo_codes"), "qty": a.get("qty")})
        if name == "get_sla":
            return self._get(f"/sla/{a['order_id']}")
        if name == "find_similar_cases":
            return self._get("/cases/similar", {"domain": a.get("domain"), "symptom": a.get("symptom")})
        raise ToolError(f"Unknown tool {name}")

    # actions
    def execute_step(self, step: dict) -> dict:
        t = step["type"]
        if t not in ACTIONS:
            raise ToolError(f"Unknown action {t}")
        path, required, _ = ACTIONS[t]
        params = dict(step.get("params") or {})
        missing = [p for p in required if p not in params and not (p == "reason" and t == "hold_order")]
        if missing:
            raise ToolError(f"{t}: missing params {missing}")
        return self._post(path, params)

    def undo(self, log_id: int, reason: str | None = None) -> dict:
        """Reverse one earlier action (FR-15). Deliberately not in ACTIONS: the LLM can never propose it."""
        return self._post("/actions/undo", {"log_id": log_id, "reason": reason})


def validate_step(step: dict) -> str | None:
    """Return an error string if a proposed step is malformed, else None."""
    t = step.get("type")
    if t not in ACTIONS:
        return f"unknown action type '{t}'"
    required = [p for p in ACTIONS[t][1] if not (t == "hold_order" and p == "reason")]
    missing = [p for p in required if p not in (step.get("params") or {})]
    return f"{t} missing params {missing}" if missing else None


def compact(result: Any, limit: int = 6000) -> str:
    """Serialise a tool result for the LLM / trail, trimming very long payloads."""
    s = json.dumps(result, default=str)
    return s if len(s) <= limit else s[:limit] + '…"(truncated)"'
