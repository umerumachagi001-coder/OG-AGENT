"""n8n bridge (ADR-009). The n8n workflows run the process; these endpoints are the steps they call.

n8n owns the flow: when to scan, which systems to check for which problem, asking Claude,
the approval policy, executing, pausing for a person and resuming. Python keeps what must be
shared with the dashboard and the Python fallback engine: the record of every step (trail), the
prompt/schema, cost verification against live data, and executing actions against the systems.

Order of calls in workflow "OEA 2 · Investigate and resolve":

    POST /n8n/exceptions/{id}/start                  claim it (status → investigating)
    POST /n8n/exceptions/{id}/tool  {tool, input}    one call per evidence check; logged to the trail
    POST /n8n/exceptions/{id}/llm-request            Anthropic request body built from the evidence
         (n8n calls api.anthropic.com itself)
    POST /n8n/exceptions/{id}/playbook               rule-based diagnosis when Claude is off or fails
    POST /n8n/exceptions/{id}/prepare {resolution}   validate, save diagnosis, verify costs, return context
    POST /n8n/exceptions/{id}/step {step}            containment step (hold the order)
         (n8n evaluates the policy in its own Code node)
    POST /n8n/exceptions/{id}/decide {options, verdicts, resume_url}
    POST /n8n/actions/{id}/run {steps?, by}          execute an option (auto or after approval)
    POST /n8n/exceptions/{id}/failed                 n8n error path: hand the case to a person
"""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent import config, fallback, llm, policy as policy_mod, runner, store
from agent.tools import INVESTIGATION_TOOLS, ToolError, validate_step
from services.detector.rules import detect_once

router = APIRouter(prefix="/n8n", tags=["n8n bridge"])

# Wired by orchestrator.app so the bridge shares its pooled services client and pause flag.
svc = None          # callable returning agent.tools.Services
is_paused = None    # callable returning bool

INVESTIGATION_NAMES = {t["name"] for t in INVESTIGATION_TOOLS}


def _exc(exc_id: int) -> dict:
    e = store.get_exception(exc_id)
    if not e:
        raise HTTPException(404, f"Exception {exc_id} not found")
    return e


# ───────── resume URLs for paused n8n executions ─────────

def _ensure_wait_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS n8n_waits (exception_id INTEGER PRIMARY KEY, resume_url TEXT NOT NULL,"
                  " created_at TEXT NOT NULL)")


def save_resume_url(exc_id: int, url: str):
    conn = store.connect()
    try:
        _ensure_wait_table(conn)
        conn.execute("INSERT OR REPLACE INTO n8n_waits VALUES (?,?,?)", (exc_id, url, store.now_iso()))
        conn.commit()
    finally:
        conn.close()


def pop_resume_url(exc_id: int, keep: bool = False) -> str | None:
    conn = store.connect()
    try:
        _ensure_wait_table(conn)
        r = conn.execute("SELECT resume_url FROM n8n_waits WHERE exception_id=?", (exc_id,)).fetchone()
        if r and not keep:
            conn.execute("DELETE FROM n8n_waits WHERE exception_id=?", (exc_id,))
            conn.commit()
        return r[0] if r else None
    finally:
        conn.close()


def all_resume_urls() -> list[tuple[int, str]]:
    conn = store.connect()
    try:
        _ensure_wait_table(conn)
        return [tuple(r) for r in conn.execute("SELECT exception_id, resume_url FROM n8n_waits")]
    finally:
        conn.close()


# ───────── detection ─────────

@router.post("/scan")
def scan():
    """Run the detector rules once. Returns the exceptions it just opened (n8n investigates each)."""
    if is_paused and is_paused():
        return {"created": [], "count": 0}
    created = detect_once()
    return {"created": created, "count": len(created)}


# ───────── investigation ─────────

@router.post("/exceptions/{exc_id}/start")
def start(exc_id: int):
    e = _exc(exc_id)
    if e["status"] not in ("open", "failed"):
        return {**e, "claimed": False}
    store.set_status(exc_id, "investigating")
    order = svc().call("get_order", {"order_id": e["order_id"]})
    store.Trail(exc_id).thought(f"n8n workflow picked up the {e['domain']} exception on {e['order_id']}.",
                                source="n8n")
    return {**e, "claimed": True, "status": "investigating",
            "skus": [i["sku"] for i in order["items"]], "customer_tier": order["customer"]["tier"]}


class ToolBody(BaseModel):
    tool: str
    input: dict = {}


@router.post("/exceptions/{exc_id}/tool")
def tool(exc_id: int, body: ToolBody):
    """One evidence check (read-only), recorded on the trail exactly like the Python agent records it."""
    _exc(exc_id)
    if body.tool not in INVESTIGATION_NAMES:
        raise HTTPException(422, f"{body.tool} is not a read-only investigation tool")
    trail = store.Trail(exc_id)
    trail.tool_call(body.tool, body.input)
    try:
        data = svc().call(body.tool, body.input)
    except KeyError as e:
        trail.tool_result(body.tool, error=f"missing input {e}")
        raise HTTPException(422, f"{body.tool}: missing input {e}") from e
    except ToolError as e:
        trail.tool_result(body.tool, error=str(e))
        return {"ok": False, "tool": body.tool, "error": str(e)}
    summary = llm._summary(body.tool, data)
    trail.tool_result(body.tool, data=data, summary=summary)
    return {"ok": True, "tool": body.tool, "summary": summary, "data": data}


def _evidence(exc_id: int) -> list[dict]:
    """Everything n8n has gathered so far for this exception, from the trail."""
    out = []
    for s in store.get_trail(exc_id):
        c = s["content"]
        if s["type"] == "tool_result" and c.get("tool") in INVESTIGATION_NAMES:
            out.append({"tool": c["tool"], "ok": c["ok"], "summary": c.get("summary"),
                        "data": c.get("data"), "error": c.get("error")})
    return out


@router.post("/exceptions/{exc_id}/llm-request")
def llm_request(exc_id: int):
    """The Anthropic Messages API request n8n sends. One source of truth for prompt and output schema."""
    e = _exc(exc_id)
    enabled = runner._mode(None) == "llm"
    evidence = _evidence(exc_id)
    body = {
        "model": config.MODEL,
        "max_tokens": 2048,
        "system": llm.system_prompt(policy_mod.load()) + (
            "\n\nThe n8n workflow has already run the investigation checks for this exception. Their results are "
            "in the user message. Base your diagnosis only on them, then call submit_resolution."),
        "tools": [llm.SUBMIT_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_resolution"},
        "messages": [{"role": "user", "content": llm.user_prompt(e) + "\n\nEvidence gathered by the workflow:\n" +
                      json.dumps([{k: v for k, v in x.items() if k != "summary"} for x in evidence], default=str)[:60000]}],
    }
    return {"enabled": enabled, "model": config.MODEL, "evidence_count": len(evidence), "request": body}


class _NullTrail:
    """The rule playbook re-reads the systems; n8n has already logged those checks, so don't log twice."""
    def __getattr__(self, _name):
        return lambda *a, **k: None


@router.post("/exceptions/{exc_id}/playbook")
def playbook(exc_id: int, reason: str | None = None):
    e = _exc(exc_id)
    if reason:
        store.Trail(exc_id).decision("fallback", reason=reason, note="n8n switched to the rule playbook")
    else:
        store.Trail(exc_id).thought("Claude is switched off (no ANTHROPIC_API_KEY), so the rule playbook diagnoses "
                                    "this from the evidence above.", source="n8n")
    try:
        res = fallback.run(e, svc(), _NullTrail())
    except ToolError as err:
        raise HTTPException(502, f"systems unreachable: {err}") from err
    return {"source": "rules", "resolution": res}


class PrepareBody(BaseModel):
    resolution: dict
    source: str = "llm"


@router.post("/exceptions/{exc_id}/prepare")
def prepare(exc_id: int, body: PrepareBody):
    """Validate the diagnosis, record it, re-price every option from live data, return the policy inputs."""
    e = _exc(exc_id)
    res = body.resolution
    errors = llm.validate_resolution(res)
    if errors:
        return {"valid": False, "errors": errors}
    trail = store.Trail(exc_id)
    for t in res.get("thoughts") or []:
        trail.thought(t, source=body.source)
    store.save_diagnosis(exc_id, res["root_cause"], float(res["confidence"]))
    trail.decision("diagnosis", source=body.source, root_cause=res["root_cause"], confidence=res["confidence"],
                   evidence=res.get("evidence", []), customer_message=res.get("customer_message"))
    options = res["options"][:3]
    runner._verify_costs(svc(), options, trail)
    ctx = runner._context(svc(), e["order_id"])
    ctx["confidence"] = float(res["confidence"])
    pol = policy_mod.load()
    containment = [s for s in (res.get("containment") or [])
                   if validate_step(s) is None and policy_mod.containment_allowed(s, pol)]
    return {"valid": True, "exception_id": exc_id, "context": ctx, "options": options,
            "containment": containment, "policy": {k: v for k, v in pol.items() if not k.startswith("_")}}


class StepBody(BaseModel):
    step: dict
    kind: str = "containment"


@router.post("/exceptions/{exc_id}/step")
def step(exc_id: int, body: StepBody):
    _exc(exc_id)
    if validate_step(body.step):
        raise HTTPException(422, validate_step(body.step))
    if body.kind == "containment" and not policy_mod.containment_allowed(body.step):
        raise HTTPException(422, f"{body.step['type']} is not allowed as containment")
    ok, results, cost = runner._run_steps(svc(), [body.step], store.Trail(exc_id), body.kind)
    return {"ok": ok, "results": results, "cost": cost}


class DecideBody(BaseModel):
    options: list[dict]
    verdicts: list[dict]          # [{auto, reasons, checks}] from the n8n policy node, same order as options
    context: dict = {}
    resume_url: str | None = None


@router.post("/exceptions/{exc_id}/decide")
def decide(exc_id: int, body: DecideBody):
    """Store the ranked options with n8n's policy verdicts; escalate or hand back the auto-run option."""
    e = _exc(exc_id)
    if len(body.options) != len(body.verdicts) or not body.options:
        raise HTTPException(422, "options and verdicts must be non-empty and the same length")
    trail = store.Trail(exc_id)
    saved = []
    for rank, (opt, v) in enumerate(zip(body.options, body.verdicts), 1):
        v = {"auto": bool(v.get("auto")), "reasons": v.get("reasons", []), "checks": v.get("checks", [])}
        aid = store.save_option(exc_id, rank, opt, v)
        saved.append({"action_id": aid, "rank": rank, "title": opt.get("title"), "cost": opt.get("cost"), **v})
    ctx = body.context
    trail.decision("policy", engine="n8n",
                   context={k: ctx.get(k) for k in ("customer_tier", "order_value", "confidence", "fraud_score")},
                   options=[{"rank": s["rank"], "title": s["title"], "auto": s["auto"], "reasons": s["reasons"]}
                            for s in saved])
    top = saved[0]
    if top["auto"]:
        return {"auto": True, "action_id": top["action_id"], "steps": body.options[0]["steps"], "top": top}
    trail.decision("escalation", reason="; ".join(top["reasons"]), recommended=top["title"])
    store.set_status(exc_id, "awaiting_approval")
    if body.resume_url:
        save_resume_url(exc_id, body.resume_url)
    dash = os.environ.get("OEA_DASHBOARD_URL", "http://localhost:8001")
    order = svc().call("get_order", {"order_id": e["order_id"]})
    text = (f":warning: *{e['order_id']}* needs a decision ({e['severity']} {e['domain']} problem, "
            f"{order['customer']['name']} – {order['customer']['tier']}, ₹{order['total']:,.0f})\n"
            f"*Root cause:* {store.get_exception(exc_id)['root_cause']}\n"
            f"*Agent's pick:* {top['title']} (₹{float(top['cost'] or 0):,.0f})\n"
            f"*Why it needs you:* {'; '.join(top['reasons'])}\n"
            f"Approve or reject: {dash}/ui/")
    return {"auto": False, "top": top, "options": saved, "notify_text": text, "dashboard_url": f"{dash}/ui/"}


class RunBody(BaseModel):
    steps: list[dict] | None = None
    by: str = "agent"


@router.post("/actions/{action_id}/run")
def run(action_id: int, body: RunBody | None = None):
    """Execute an option: right away when policy allowed it (by=agent), or after a person approved it."""
    body = body or RunBody()
    a = store.get_action(action_id)
    if not a:
        raise HTTPException(404, f"Action {action_id} not found")
    if a["status"] in ("executed", "rejected"):
        return {"action_id": action_id, "status": a["status"], "skipped": True}
    exc_id = a["exception_id"]
    if body.by != "agent":
        return {**runner.approve(action_id, by=body.by, steps=body.steps, svc=svc()), "engine": "n8n"}
    trail = store.Trail(exc_id)
    ok, results, cost = runner._run_steps(svc(), body.steps or a["params"]["steps"], trail, "execution")
    if ok:
        store.set_action_status(action_id, "executed")
        store.set_status(exc_id, "auto_resolved", resolved=True)
        store.write_resolution(exc_id, action_id, "success", "agent")
        return {"exception_id": exc_id, "action_id": action_id, "status": "auto_resolved", "actual_cost": cost,
                "results": results, "engine": "n8n"}
    store.set_action_status(action_id, "failed")
    trail.decision("escalation", reason="Auto-execution failed; a human must choose", results=results)
    store.set_status(exc_id, "awaiting_approval")
    return {"exception_id": exc_id, "action_id": action_id, "status": "failed", "results": results, "engine": "n8n"}


@router.post("/exceptions/{exc_id}/failed")
def failed(exc_id: int, error: dict[str, Any] | None = None):
    """n8n error path: mark the case so a person sees it (dashboard shows 'Investigate again')."""
    _exc(exc_id)
    store.Trail(exc_id).decision("fallback", reason=f"n8n workflow error: {json.dumps(error or {})[:400]}",
                                 note="Case handed to a person")
    store.set_status(exc_id, "failed")
    return {"exception_id": exc_id, "status": "failed"}
