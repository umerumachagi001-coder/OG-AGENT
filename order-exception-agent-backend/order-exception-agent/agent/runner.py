"""Agent entry points (owner: P2). P4's orchestrator API calls these three functions:

    investigate(exception_id)                       → DETECT ✔ → INVESTIGATE → DECIDE → ACT / ESCALATE
    approve(action_id, by="Ops: Ruhaan", steps=None) → human approves (optionally modified steps, FR-13)
    reject(action_id, by=..., feedback=...)          → human rejects (FR-13, FR-20)

All three return a JSON-serialisable summary and write every step to investigation_trail.
"""
from __future__ import annotations

import os
from typing import Any

from agent import config, fallback, llm, policy as policy_mod, store
from agent.tools import Services, ToolError, validate_step


# ───────── context & cost verification (don't trust the LLM's numbers) ─────────

def _context(svc: Services, order_id: str) -> dict:
    o = svc.call("get_order", {"order_id": order_id})
    ctx = {"customer_tier": o["customer"]["tier"], "order_value": o["total"], "order_status": o["status"]}
    try:
        p = svc.call("get_payment_status", {"order_id": order_id})
        ctx["fraud_score"] = p["risk_signals"].get("max_fraud_score")
        ctx["payment_attempts"] = len(p["attempts"])
    except ToolError:
        pass
    return ctx


def _estimate_cost(svc: Services, step: dict, cache: dict) -> float:
    t, p = step["type"], step.get("params") or {}
    try:
        if t == "reallocate_stock":
            whs = cache.setdefault("wh", {w["id"]: w for w in svc.http.get("/warehouses").json()})
            order = svc.call("get_order", {"order_id": p["order_id"]})
            units = sum(i["qty"] for i in order["items"])
            return units * whs[p["to"]]["transfer_cost_per_unit"]
        if t == "expedite_shipping":
            cars = cache.setdefault("car", {c["id"]: c for c in svc.http.get("/carriers").json()})
            return cars[p["carrier"]]["base_cost"]
        if t == "switch_supplier":
            pos = svc.call("get_supplier_status", {"po_id": p["po_id"]})["purchase_orders"]
            alt = next(a for a in pos[0]["alternates"] if a["supplier_id"] == p["supplier_id"])
            return alt["extra_cost_vs_current"]
    except Exception:  # noqa: BLE001 — estimation is best-effort; fall back to the agent's figure
        return float(step.get("est_cost") or 0)
    return 0.0


def _verify_costs(svc: Services, options: list[dict], trail: store.Trail):
    cache: dict = {}
    for o in options:
        for s in o.get("steps") or []:
            s["est_cost"] = _estimate_cost(svc, s, cache)
        verified = round(sum(s["est_cost"] for s in o["steps"]), 2)
        claimed = float(o.get("cost") or 0)
        if abs(verified - claimed) > 1 and not any(s["type"] == "release_order" for s in o["steps"]):
            trail.decision("cost_check", option=o.get("title"), claimed=claimed, verified=verified,
                           note="Using system-verified cost for policy")
            o["cost"] = verified


# ───────── execution ─────────

def _run_steps(svc: Services, steps: list[dict], trail: store.Trail, kind: str) -> tuple[bool, list[dict], float]:
    results, total = [], 0.0
    for s in steps:
        trail.tool_call(s["type"], s.get("params") or {})
        try:
            r = svc.execute_step(s)
        except ToolError as e:
            trail.tool_result(s["type"], error=str(e))
            return False, results + [{"ok": False, "message": str(e), "action": s["type"]}], total
        trail.tool_result(s["type"], data=r, summary=r.get("message", ""), error=None if r.get("ok") else r.get("message"))
        results.append(r)
        total += float(r.get("cost") or 0)
        if not r.get("ok"):
            return False, results, total
    trail.decision(kind, ok=True, steps=[r.get("message") for r in results], actual_cost=round(total, 2))
    return True, results, total


def _mode(requested: str | None) -> str:
    m = requested or config.AGENT_MODE
    if m == "auto":
        return "llm" if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") else "rules"
    return m


# ───────── public API ─────────

def investigate(exception_id: int, svc: Services | None = None, client: Any | None = None,
                mode: str | None = None) -> dict:
    svc = svc or Services()
    exc = store.get_exception(exception_id)
    if not exc:
        raise ValueError(f"Exception {exception_id} not found")
    if exc["status"] not in ("open", "failed"):
        return {"exception_id": exception_id, "status": exc["status"], "skipped": "already handled"}

    pol = policy_mod.load()
    store.set_status(exception_id, "investigating")
    trail = store.Trail(exception_id)
    used = _mode(mode)

    res = None
    if used == "llm":
        try:
            res = llm.investigate(exc, svc, trail, pol, client=client)
            source = "llm"
        except llm.LLMFailure as e:
            trail.decision("fallback", reason=str(e), note="Switching to rule-based playbook (NFR-5)")
    if res is None:
        try:
            res = fallback.run(exc, svc, trail)
            source = "rules"
        except ToolError as e:
            trail.decision("escalation", reason=f"Systems unreachable during investigation: {e}")
            store.set_status(exception_id, "awaiting_approval")
            return {"exception_id": exception_id, "status": "awaiting_approval", "error": str(e)}

    store.save_diagnosis(exception_id, res["root_cause"], float(res["confidence"]))
    trail.decision("diagnosis", source=source, root_cause=res["root_cause"], confidence=res["confidence"],
                   evidence=res.get("evidence", []), customer_message=res.get("customer_message"))

    ctx = _context(svc, exc["order_id"])
    ctx["confidence"] = float(res["confidence"])

    # containment first (e.g. hold the order) — protective, allowed even when the fix escalates
    contained = []
    for step in res.get("containment") or []:
        if validate_step(step) is None and policy_mod.containment_allowed(step, pol):
            ok, r, _ = _run_steps(svc, [step], trail, "containment")
            contained.append({"step": step, "ok": ok})
        else:
            trail.decision("containment", ok=False, step=step, note="Not in containment_allowed; skipped")

    options = res["options"][:3]
    _verify_costs(svc, options, trail)
    if contained:
        ctx["order_status"] = "on_hold"

    saved = []
    for rank, opt in enumerate(options, 1):
        v = policy_mod.evaluate(opt, ctx, pol)
        aid = store.save_option(exception_id, rank, opt, v.as_dict())
        saved.append({"action_id": aid, "rank": rank, "title": opt.get("title"), "auto": v.auto,
                      "reasons": v.reasons, "cost": opt.get("cost")})
    trail.decision("policy", context={k: ctx.get(k) for k in ("customer_tier", "order_value", "confidence", "fraud_score")},
                   options=[{"rank": s["rank"], "title": s["title"], "auto": s["auto"], "reasons": s["reasons"]} for s in saved])

    top = saved[0]
    if top["auto"]:
        ok, results, cost = _run_steps(svc, options[0]["steps"], trail, "execution")
        if ok:
            store.set_action_status(top["action_id"], "executed")
            store.set_status(exception_id, "auto_resolved", resolved=True)
            store.write_resolution(exception_id, top["action_id"], "success", "agent")
            return {"exception_id": exception_id, "status": "auto_resolved", "source": source,
                    "root_cause": res["root_cause"], "executed": top, "actual_cost": cost, "results": results}
        store.set_action_status(top["action_id"], "failed")
        trail.decision("escalation", reason="Auto-execution failed; a human must choose", results=results)
    else:
        trail.decision("escalation", reason="; ".join(top["reasons"]), recommended=top["title"])
    store.set_status(exception_id, "awaiting_approval")
    return {"exception_id": exception_id, "status": "awaiting_approval", "source": source,
            "root_cause": res["root_cause"], "options": saved, "containment": contained}


def approve(action_id: int, by: str = "human", steps: list[dict] | None = None, svc: Services | None = None) -> dict:
    svc = svc or Services()
    a = store.get_action(action_id)
    if not a:
        raise ValueError(f"Action {action_id} not found")
    if a["status"] not in ("proposed", "failed"):
        return {"action_id": action_id, "status": a["status"], "skipped": "not pending"}
    exc_id = a["exception_id"]
    trail = store.Trail(exc_id)
    run = steps or a["params"]["steps"]
    errs = [e for e in (validate_step(s) for s in run) if e]
    if errs:
        raise ValueError(f"Invalid steps: {errs}")
    trail.decision("human", verdict="approved", by=by, action_id=action_id, title=a["params"].get("title"),
                   modified=steps is not None)
    store.set_action_status(action_id, "approved")
    ok, results, cost = _run_steps(svc, run, trail, "execution")
    if ok:
        store.set_action_status(action_id, "executed")
        for other in store.get_actions(exc_id):
            if other["id"] != action_id and other["status"] == "proposed":
                store.set_action_status(other["id"], "rejected")
        store.set_status(exc_id, "resolved", resolved=True)
        store.write_resolution(exc_id, action_id, "success", by, "modified" if steps else None)
        return {"exception_id": exc_id, "action_id": action_id, "status": "resolved", "actual_cost": cost, "results": results}
    store.set_action_status(action_id, "failed")
    store.set_status(exc_id, "awaiting_approval")
    return {"exception_id": exc_id, "action_id": action_id, "status": "failed", "results": results}


def reject(action_id: int, by: str = "human", feedback: str | None = None) -> dict:
    a = store.get_action(action_id)
    if not a:
        raise ValueError(f"Action {action_id} not found")
    exc_id = a["exception_id"]
    store.set_action_status(action_id, "rejected")
    store.Trail(exc_id).decision("human", verdict="rejected", by=by, action_id=action_id,
                                 title=a["params"].get("title"), feedback=feedback)
    pending = [x for x in store.get_actions(exc_id) if x["status"] in ("proposed", "failed")]
    if not pending:
        store.set_status(exc_id, "rejected", resolved=True)
        store.write_resolution(exc_id, None, "rejected", by, feedback)
    return {"exception_id": exc_id, "action_id": action_id, "status": "rejected", "remaining_options": len(pending)}
