"""Claude investigation loop (owner: P2, FR-5, FR-6, FR-7, FR-9).

think → call a read-only tool → record to trail → … → submit_resolution (structured output).
The model never executes actions; it proposes them. Policy + runner decide what runs.
"""
from __future__ import annotations

import json
import time
from typing import Any

from agent import config
from agent.store import Trail
from agent.tools import ACTIONS, INVESTIGATION_TOOLS, Services, ToolError, compact, validate_step


class LLMFailure(Exception):
    pass


STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(ACTIONS)},
        "params": {"type": "object"},
    },
    "required": ["type", "params"],
}

SUBMIT_TOOL = {
    "name": "submit_resolution",
    "description": "Submit your diagnosis and ranked options. Call exactly once, when the investigation is complete.",
    "input_schema": {
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "description": "The underlying cause, not the symptom. One or two sentences."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {
                "type": "array",
                "description": "Facts from tool results that prove the root cause. Cite the tool.",
                "items": {"type": "object", "properties": {
                    "source": {"type": "string", "description": "tool name"},
                    "fact": {"type": "string"}}, "required": ["source", "fact"]},
            },
            "containment": {
                "type": "array", "items": STEP_SCHEMA,
                "description": "Protective steps to take immediately whatever is decided (e.g. hold_order when the order must not ship/charge as is). Usually empty.",
            },
            "options": {
                "type": "array", "minItems": 1, "maxItems": 3,
                "description": "1–3 resolution options, best first. Each option is a list of steps executed in order.",
                "items": {"type": "object", "properties": {
                    "title": {"type": "string", "description": "Short label, e.g. 'Ship from Mumbai Hub'"},
                    "steps": {"type": "array", "items": STEP_SCHEMA, "minItems": 1},
                    "cost": {"type": "number", "description": "Total extra cost in INR"},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "impact": {"type": "string", "description": "What happens for the customer/business if chosen"},
                    "rationale": {"type": "string"},
                }, "required": ["title", "steps", "cost", "risk", "rationale"]},
            },
            "customer_message": {"type": "string", "description": "Optional draft notification for the customer if the order is delayed or changed."},
        },
        "required": ["root_cause", "confidence", "evidence", "options"],
    },
}


def system_prompt(policy: dict) -> str:
    actions = "\n".join(f"- {k}({', '.join(v[1])}): {v[2]}" for k, v in ACTIONS.items())
    return f"""You are the exception desk agent for an Indian e-commerce order pipeline. Normal automation has
stopped on an order. Your job: find the ROOT CAUSE (not the symptom) using the read-only tools, then
propose 1–3 ranked resolution options via submit_resolution.

How to work:
- Start with get_order. Then check every system that could explain the symptom. Verify, don't assume:
  a symptom in one domain often has a cause in another (a stock problem can become a deadline problem).
- Check find_similar_cases once to reuse what worked before.
- Before proposing an option, confirm it actually fixes the problem with data (e.g. verified stock exists in
  the target warehouse; the new ETA meets the promised date; the price respects the margin floor).
  If one step alone doesn't fix it, combine steps in one option (e.g. switch_supplier then expedite_shipping).
- Never propose retry_payment when the card is expired or fraud signals are present.
- Use hold_order as containment when the order must not progress as is (wrong price, likely fraud).
- Costs are INR. Use the exact figures from tool results.
- Keep each thought to one or two sentences. Be fast: you have at most {config.MAX_TOOL_CALLS} tool calls.

Action types you may propose (they are executed later, not by you):
{actions}

Approval policy (for your awareness — a separate engine enforces it, so rank by what is BEST, not by what
avoids approval):
{json.dumps({k: v for k, v in policy.items() if not k.startswith('_')}, indent=1)}
"""


def user_prompt(exc: dict) -> str:
    return (f"Exception #{exc['id']} on order {exc['order_id']}\n"
            f"Detected domain: {exc['domain']} · severity: {exc['severity']}\n"
            f"Symptom: {exc.get('symptom') or 'n/a'}\n\nInvestigate and submit your resolution.")


def validate_resolution(res: dict) -> list[str]:
    errs = []
    if not isinstance(res.get("root_cause"), str) or not res["root_cause"].strip():
        errs.append("root_cause missing")
    c = res.get("confidence")
    if not isinstance(c, (int, float)) or not 0 <= c <= 1:
        errs.append("confidence must be 0–1")
    opts = res.get("options") or []
    if not 1 <= len(opts) <= 3:
        errs.append("need 1–3 options")
    for i, o in enumerate(opts, 1):
        for s in o.get("steps") or []:
            e = validate_step(s)
            if e:
                errs.append(f"option {i}: {e}")
        if not o.get("steps"):
            errs.append(f"option {i}: no steps")
    for s in res.get("containment") or []:
        e = validate_step(s)
        if e:
            errs.append(f"containment: {e}")
    return errs


def _d(iso: str) -> str:
    from datetime import datetime
    dt = datetime.fromisoformat(iso)
    return f"{dt.day} {dt:%b}"   # no %-d: it breaks on Windows


def _summary(name: str, data: Any) -> str:
    """One-line plain-language summary of a tool result for the dashboard trail."""
    try:
        if name == "get_order":
            return (f"{data['customer']['name']} ({data['customer']['tier']}), ₹{data['total']:,.0f}, "
                    f"status {data['status'].replace('_', ' ')}")
        if name == "check_inventory":
            parts = []
            for l in data["locations"]:
                if l["scan_mismatch"]:
                    parts.append(f"{l['warehouse_id']} system says {l['on_hand']} but the shelf scan found {l['last_scan_qty']}")
                else:
                    parts.append(f"{l['warehouse_id']} has {l['verified_available']} free")
            return "; ".join(parts)
        if name == "get_supplier_status":
            return "; ".join(
                f"{p['id']} from {p['supplier']['name']} is {p['status']}, arriving in {p['days_until_arrival']:g} days"
                + (f" ({len(p['alternates'])} alternate supplier{'s' if len(p['alternates']) != 1 else ''})" if p["alternates"] else "")
                for p in data["purchase_orders"]) or "No purchase orders"
        if name == "get_payment_status":
            sg = data["risk_signals"]
            last = data["attempts"][-1] if data["attempts"] else {}
            bits = [f"{sg.get('decline_count', 0)} declined attempt(s)"]
            if last.get("decline_code"):
                bits.append(f"last reason {last['decline_code'].replace('_', ' ')}")
            bits.append(f"fraud score {sg.get('max_fraud_score', 0):.2f}")
            if sg.get("card_expired"):
                bits.append("card expired")
            if sg.get("ip_billing_country_mismatch"):
                bits.append("IP country differs from billing country")
            return ", ".join(bits)
        if name == "get_pricing_rules":
            charged = data.get("charged_total", data["price_as_applied"])
            v = len(data["violations"])
            return (f"Charged ₹{charged:,.2f}; correct price under the rules is ₹{data['price_by_rules']:,.2f}; "
                    + (f"{v} rule{'s' if v != 1 else ''} broken" if v else "no rules broken"))
        if name == "get_sla":
            late = data["at_risk"]
            return (f"Promised {_d(data['promised_date'])}, now arriving {_d(data['current_eta'])}"
                    + (" (late)" if late else " (on time)"))
        if name == "find_similar_cases":
            n = len(data["cases"])
            if not n:
                return "No similar past cases"
            top = data["cases"][0]
            return f"{n} similar past case{'s' if n != 1 else ''}; closest was fixed by {top['action_type'].replace('_', ' ')}"
    except Exception:  # noqa: BLE001 — summary is best-effort
        pass
    return ""


def investigate(exc: dict, svc: Services, trail: Trail, policy: dict, client: Any | None = None) -> dict:
    """Run the Claude tool loop. Returns a validated resolution dict or raises LLMFailure."""
    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic(timeout=config.LLM_TIMEOUT_S, max_retries=1)
        except Exception as e:  # noqa: BLE001
            raise LLMFailure(f"Claude client unavailable: {e}") from e

    tools = INVESTIGATION_TOOLS + [SUBMIT_TOOL]
    messages: list[dict] = [{"role": "user", "content": user_prompt(exc)}]
    system = system_prompt(policy)
    calls = 0
    started = time.monotonic()

    for _turn in range(config.MAX_TOOL_CALLS + 3):
        force = calls >= config.MAX_TOOL_CALLS or time.monotonic() - started > config.LLM_TIMEOUT_S
        try:
            resp = client.messages.create(
                model=config.MODEL, max_tokens=2048, system=system, tools=tools, messages=messages,
                tool_choice={"type": "tool", "name": "submit_resolution"} if force else {"type": "auto"},
            )
        except Exception as e:  # noqa: BLE001 — any API failure → fallback
            raise LLMFailure(f"Claude API error: {type(e).__name__}: {e}") from e

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                trail.thought(block.text)
            elif btype == "tool_use":
                if block.name == "submit_resolution":
                    res = dict(block.input)
                    errs = validate_resolution(res)
                    if not errs:
                        return res
                    if force:
                        raise LLMFailure(f"Invalid resolution: {errs}")
                    trail.thought(f"Resolution rejected by validator: {'; '.join(errs)}", source="system")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                    "content": f"Invalid: {errs}. Fix and call submit_resolution again."})
                    continue
                calls += 1
                trail.tool_call(block.name, block.input)
                try:
                    data = svc.call(block.name, block.input)
                    trail.tool_result(block.name, data=data, summary=_summary(block.name, data))
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": compact(data)})
                except (ToolError, KeyError) as e:
                    trail.tool_result(block.name, error=str(e))
                    results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": str(e)})
        if not results:
            # model answered in text without calling a tool — nudge it to submit
            messages.append({"role": "user", "content": "Call submit_resolution with your diagnosis now."})
        else:
            messages.append({"role": "user", "content": results})

    raise LLMFailure("No resolution submitted within step budget")
