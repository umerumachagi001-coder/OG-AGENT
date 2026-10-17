"""Policy engine (owner: P2, FR-10, FR-11, NFR-2).

Decides, for each proposed option, whether it may run without a human. Pure function of
(option, context, policy) — no LLM involved, so the guardrail can't be talked around.

Context is built by the runner from the source systems, not from the LLM's claims:
    customer_tier, order_value, confidence, fraud_score, payment_attempts
Step costs are re-estimated by the runner from live data before evaluation (est_cost).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent import config


@dataclass
class Verdict:
    auto: bool
    reasons: list[str] = field(default_factory=list)   # why it needs a human (empty if auto)
    checks: list[str] = field(default_factory=list)    # rules that passed (shown in the trail)

    def as_dict(self):
        return {"auto": self.auto, "reasons": self.reasons, "checks": self.checks}


def load(path: Path | None = None) -> dict:
    return json.loads(Path(path or config.POLICY_PATH).read_text())


def evaluate(option: dict, ctx: dict, policy: dict | None = None) -> Verdict:
    p = policy or load()
    auto_rules: dict = p.get("auto_execute", {})
    always = set(p.get("always_escalate", []))
    esc = p.get("escalate_if", {})
    reasons: list[str] = []
    checks: list[str] = []

    steps = option.get("steps") or []
    if not steps:
        reasons.append("option has no executable steps")

    # Order-level escalation triggers
    tier = ctx.get("customer_tier")
    if tier in esc.get("customer_tier", []):
        reasons.append(f"customer tier is {tier}")
    value = ctx.get("order_value") or 0
    if "order_value_above" in esc:
        if value > esc["order_value_above"]:
            reasons.append(f"order value ₹{value:,.0f} > ₹{esc['order_value_above']:,.0f}")
        else:
            checks.append(f"order value ₹{value:,.0f} ≤ ₹{esc['order_value_above']:,.0f}")
    conf = ctx.get("confidence")
    if conf is not None and "confidence_below" in esc:
        if conf < esc["confidence_below"]:
            reasons.append(f"confidence {conf:.2f} < {esc['confidence_below']}")
        else:
            checks.append(f"confidence {conf:.2f} ≥ {esc['confidence_below']}")

    fraud = ctx.get("fraud_score")
    if fraud is not None and "fraud_score_above" in esc and fraud > esc["fraud_score_above"]:
        reasons.append(f"payment fraud score {fraud:.2f} > {esc['fraud_score_above']}")

    if option.get("risk") in esc.get("option_risk", []):
        reasons.append(f"option risk is {option.get('risk')}")

    total = sum(float(s.get("est_cost") or 0) for s in steps)
    if "max_option_cost" in p:
        if total > p["max_option_cost"]:
            reasons.append(f"total cost ₹{total:,.0f} > ₹{p['max_option_cost']:,.0f}")

    # Step-level rules
    for s in steps:
        t = s.get("type")
        cost = float(s.get("est_cost") or 0)
        if t in always:
            reasons.append(f"{t} always needs approval")
            continue
        if t not in auto_rules:
            reasons.append(f"{t} is not auto-executable")
            continue
        rule = auto_rules[t] or {}
        lim = rule.get("max_extra_cost", rule.get("max_cost"))
        if lim is not None:
            if cost > lim:
                reasons.append(f"{t} cost ₹{cost:,.0f} > limit ₹{lim:,.0f}")
            else:
                checks.append(f"{t} cost ₹{cost:,.0f} ≤ ₹{lim:,.0f}")
        if "max_attempts" in rule:
            n = ctx.get("payment_attempts", 0)
            if n >= rule["max_attempts"]:
                reasons.append(f"already {n} payment attempts (max {rule['max_attempts']})")
            else:
                checks.append(f"{n} prior attempts < {rule['max_attempts']}")
        if "fraud_score_below" in rule:
            f = ctx.get("fraud_score")
            if f is None or f >= rule["fraud_score_below"]:
                reasons.append(f"fraud score {f} ≥ {rule['fraud_score_below']}")
            else:
                checks.append(f"fraud score {f} < {rule['fraud_score_below']}")

    return Verdict(auto=not reasons, reasons=reasons, checks=checks)


def containment_allowed(step: dict, policy: dict | None = None) -> bool:
    """Protective steps (e.g. hold_order) run immediately, even when the fix itself needs a human."""
    p = policy or load()
    return step.get("type") in p.get("containment_allowed", [])
