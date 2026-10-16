"""Agent end-to-end tests against the real mock services (owner: P2).   Run: make test-agent

Uses FastAPI's TestClient as the HTTP transport, so no server needs to be running.
The LLM path is exercised with a scripted fake Claude client (no API key needed).
"""
import os
import tempfile
from types import SimpleNamespace as NS

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "agent_test.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent import policy, runner, store  # noqa: E402
from agent.tools import Services  # noqa: E402
from services.app import app  # noqa: E402

http = TestClient(app)
svc = Services(http)


@pytest.fixture(autouse=True)
def clean():
    http.post("/admin/reset")


def _exc(trigger, order, domain, symptom, severity="medium"):
    http.post(f"/admin/trigger?fail={trigger}")
    return store.create_exception(order, domain, severity, symptom)


def _kinds(eid):
    return [s["content"].get("kind") for s in store.get_trail(eid) if s["type"] == "decision"]


# ───────── rule-based path (NFR-5) — also the reference behaviour ─────────

def test_sc1_auto_resolves():
    eid = _exc("sc1", "ORD-1001", "inventory", "Pick failed: SKU-HDPH not found at bin")
    r = runner.investigate(eid, svc=svc, mode="rules")
    assert r["status"] == "auto_resolved" and r["actual_cost"] == 120
    o = http.get("/orders/ORD-1001").json()
    assert o["warehouse_id"] == "WH-B" and o["status"] == "allocated"
    e = store.get_exception(eid)
    assert "Phantom stock" in e["root_cause"] and e["confidence"] >= 0.9
    assert {"diagnosis", "policy", "execution"} <= set(_kinds(eid))
    assert http.get(f"/cases/similar?domain=inventory").json()["cases"]  # resolution stored
    assert store.get_trail(eid)[0]["type"] == "thought"


def test_sc2_escalates_with_ranked_options_then_approve():
    eid = _exc("sc2", "ORD-1002", "supplier", "PO-2001 delayed; SLA at risk", "high")
    r = runner.investigate(eid, svc=svc, mode="rules")
    assert r["status"] == "awaiting_approval"
    acts = store.get_actions(eid)
    top = acts[0]
    assert top["action_type"] == "switch_supplier+expedite_shipping" and top["cost"] == 2350
    assert top["requires_approval"] == 1
    assert any("always needs approval" in x for x in top["params"]["policy"]["reasons"])
    assert acts[-1]["action_type"] == "notify_customer"
    a = runner.approve(top["id"], by="Ops: Test", svc=svc)
    assert a["status"] == "resolved"
    assert not http.get("/sla/ORD-1002").json()["at_risk"]
    assert store.get_exception(eid)["status"] == "resolved"
    assert [x["status"] for x in store.get_actions(eid)][1:] == ["rejected"] * (len(acts) - 1)


def test_sc3_expired_card_notifies_not_retries():
    eid = _exc("sc3_expired", "ORD-1003", "payment", "Payment declined")
    r = runner.investigate(eid, svc=svc, mode="rules")
    assert r["status"] == "auto_resolved"
    assert r["executed"]["title"] == "Send payment-update link"
    assert len(http.get("/payments/ORD-1003").json()["attempts"]) == 1   # no retry happened


def test_sc3_fraud_holds_and_escalates():
    eid = _exc("sc3_fraud", "ORD-1003", "payment", "Payment declined", "high")
    r = runner.investigate(eid, svc=svc, mode="rules")
    assert r["status"] == "awaiting_approval"
    assert r["containment"][0]["ok"]
    assert http.get("/orders/ORD-1003").json()["status"] == "on_hold"
    reasons = store.get_actions(eid)[0]["params"]["policy"]["reasons"]
    assert any("fraud score" in x for x in reasons)


def test_sc4_holds_and_proposes_correction_then_reject_and_approve():
    eid = _exc("sc4", "ORD-1004", "pricing", "Order total below margin floor", "high")
    r = runner.investigate(eid, svc=svc, mode="rules")
    assert r["status"] == "awaiting_approval"
    assert http.get("/orders/ORD-1004").json()["status"] == "on_hold"
    acts = store.get_actions(eid)
    assert acts[0]["params"]["steps"][0]["params"]["price"] == 15199.2
    assert runner.reject(acts[1]["id"], by="Ops", feedback="no")["remaining_options"] == 1
    assert runner.approve(acts[0]["id"], by="Ops", svc=svc)["status"] == "resolved"
    o = http.get("/orders/ORD-1004").json()
    assert o["total"] == 15199.2 and o["promo_codes"] == ["FEST20"]


def test_service_outage_escalates_gracefully():
    class Down(Services):
        def call(self, name, args):
            from agent.tools import ToolError
            raise ToolError("HTTP 503")
    eid = _exc("sc1", "ORD-1001", "inventory", "Pick failed")
    r = runner.investigate(eid, svc=Down(http), mode="rules")
    assert r["status"] == "awaiting_approval" and "error" in r


# ───────── policy unit checks ─────────

def test_policy_rules():
    pol = policy.load()
    realloc = {"steps": [{"type": "reallocate_stock", "est_cost": 120}]}
    assert policy.evaluate(realloc, {"customer_tier": "Gold", "order_value": 10000, "confidence": 0.9}, pol).auto
    assert not policy.evaluate(realloc, {"customer_tier": "VIP", "order_value": 10000, "confidence": 0.9}, pol).auto
    assert not policy.evaluate(realloc, {"customer_tier": "Gold", "order_value": 60000, "confidence": 0.9}, pol).auto
    assert not policy.evaluate(realloc, {"customer_tier": "Gold", "order_value": 100, "confidence": 0.5}, pol).auto
    assert not policy.evaluate({"steps": [{"type": "reallocate_stock", "est_cost": 900}]},
                               {"order_value": 1, "confidence": 1}, pol).auto
    retry = {"steps": [{"type": "retry_payment"}]}
    assert policy.evaluate(retry, {"order_value": 1, "confidence": 1, "fraud_score": 0.1, "payment_attempts": 1}, pol).auto
    assert not policy.evaluate(retry, {"order_value": 1, "confidence": 1, "fraud_score": 0.4, "payment_attempts": 1}, pol).auto
    assert not policy.evaluate(retry, {"order_value": 1, "confidence": 1, "fraud_score": 0.1, "payment_attempts": 2}, pol).auto


# ───────── LLM path with a scripted fake Claude ─────────

def _tool(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClaude:
    """Replays a script of responses; records what the agent sent."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return NS(content=nxt)


GOOD_SC1 = {
    "root_cause": "WH-A system count is phantom: scan found 0 of 5.", "confidence": 0.9,
    "evidence": [{"source": "check_inventory", "fact": "WH-A on_hand 5, last scan 0"}],
    "options": [{"title": "Ship from Mumbai", "cost": 50, "risk": "low", "rationale": "verified stock",
                 "steps": [{"type": "reallocate_stock", "params": {"order_id": "ORD-1001", "from": "WH-A", "to": "WH-B"}}]}],
}


def test_llm_loop_records_trail_and_corrects_cost():
    eid = _exc("sc1", "ORD-1001", "inventory", "Pick failed")
    fake = FakeClaude([
        [NS(type="text", text="Start with the order."), _tool("t1", "get_order", {"order_id": "ORD-1001"})],
        [_tool("t2", "check_inventory", {"sku": "SKU-HDPH"})],
        [_tool("t3", "submit_resolution", {**GOOD_SC1, "options": [{**GOOD_SC1["options"][0], "steps": [{"type": "teleport", "params": {}}]}]})],
        [_tool("t4", "submit_resolution", GOOD_SC1)],
    ])
    r = runner.investigate(eid, svc=svc, client=fake, mode="llm")
    assert r["status"] == "auto_resolved" and r["source"] == "llm"
    trail = store.get_trail(eid)
    tools = [s["content"]["tool"] for s in trail if s["type"] == "tool_call"]
    assert tools[:2] == ["get_order", "check_inventory"]
    assert any("validator" in s["content"].get("text", "") for s in trail if s["type"] == "thought")
    assert "cost_check" in _kinds(eid)          # LLM said ₹50, system says ₹120
    assert store.get_actions(eid)[0]["cost"] == 120
    assert fake.calls[0]["tools"][-1]["name"] == "submit_resolution"


def test_llm_failure_falls_back_to_rules():
    eid = _exc("sc1", "ORD-1001", "inventory", "Pick failed")
    r = runner.investigate(eid, svc=svc, client=FakeClaude([RuntimeError("529 overloaded")]), mode="llm")
    assert r["status"] == "auto_resolved" and r["source"] == "rules"
    assert "fallback" in _kinds(eid)


def test_llm_step_budget_forces_submission():
    from agent import config
    eid = _exc("sc1", "ORD-1001", "inventory", "Pick failed")
    script = [[_tool(f"t{i}", "get_order", {"order_id": "ORD-1001"})] for i in range(config.MAX_TOOL_CALLS)]
    script.append([_tool("final", "submit_resolution", GOOD_SC1)])
    fake = FakeClaude(script)
    runner.investigate(eid, svc=svc, client=fake, mode="llm")
    assert fake.calls[-1]["tool_choice"] == {"type": "tool", "name": "submit_resolution"}
