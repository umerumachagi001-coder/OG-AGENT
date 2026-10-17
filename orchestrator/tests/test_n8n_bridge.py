"""The n8n bridge, driven in the same order the n8n workflow calls it (no n8n needed).

Covers the contract the workflow JSON relies on: field names, statuses, the escalate/resume path.
The live n8n run is checked separately by n8n/e2e_check.py.
"""
import os
import tempfile

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "bridge_test.db"))
os.environ.setdefault("OEA_DISABLE_LOOP", "1")
os.environ.setdefault("OEA_AGENT_MODE", "rules")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent import policy  # noqa: E402
from agent.tools import Services  # noqa: E402
from orchestrator.app import State, app as orch_app  # noqa: E402
from services.app import app as services_app  # noqa: E402

State.services_factory = lambda: Services(TestClient(services_app))
api = TestClient(orch_app)


@pytest.fixture(autouse=True)
def clean():
    api.post("/reset")


def _break(name):
    # break the data without the orchestrator's python engine picking it up
    TestClient(services_app).post(f"/admin/trigger?fail={name}")
    r = api.post("/n8n/scan").json()
    assert r["count"] == 1
    return r["created"][0]


def _flow(exc, tools):
    """What 'OEA 2 · Investigate and resolve' does up to the decision."""
    eid = exc["id"]
    s = api.post(f"/n8n/exceptions/{eid}/start").json()
    assert s["claimed"] and s["status"] == "investigating"
    for tool, inp in tools(s):
        r = api.post(f"/n8n/exceptions/{eid}/tool", json={"tool": tool, "input": inp}).json()
        assert r["ok"], r
    req = api.post(f"/n8n/exceptions/{eid}/llm-request").json()
    assert req["enabled"] is False and req["evidence_count"] == len(tools(s))
    assert req["request"]["tool_choice"]["name"] == "submit_resolution"
    res = api.post(f"/n8n/exceptions/{eid}/playbook").json()
    prep = api.post(f"/n8n/exceptions/{eid}/prepare", json=res).json()
    assert prep["valid"]
    verdicts = [policy.evaluate(o, prep["context"], prep["policy"]).as_dict() for o in prep["options"]]
    for step in prep["containment"]:
        assert api.post(f"/n8n/exceptions/{eid}/step", json={"step": step}).json()["ok"]
    dec = api.post(f"/n8n/exceptions/{eid}/decide", json={
        "options": prep["options"], "verdicts": verdicts, "context": prep["context"],
        "resume_url": "http://127.0.0.1:9/webhook-waiting/1"}).json()
    return eid, prep, dec


def test_scan_is_idempotent():
    _break("sc1")
    assert api.post("/n8n/scan").json() == {"created": [], "count": 0}


def test_sc1_auto_path():
    exc = _break("sc1")
    eid, prep, dec = _flow(exc, lambda s: [("check_inventory", {"sku": s["skus"][0]}),
                                           ("get_sla", {"order_id": s["order_id"]}),
                                           ("find_similar_cases", {"domain": s["domain"], "symptom": s["symptom"]})])
    assert dec["auto"] is True
    out = api.post(f"/n8n/actions/{dec['action_id']}/run", json={"by": "agent"}).json()
    assert out["status"] == "auto_resolved" and out["actual_cost"] == 120
    trail = api.get(f"/exceptions/{eid}/trail").json()
    kinds = [s["content"].get("kind") for s in trail if s["type"] == "decision"]
    assert kinds == ["diagnosis", "policy", "execution"]
    assert trail[0]["content"]["source"] == "n8n"
    # already claimed: a second start is refused
    assert api.post(f"/n8n/exceptions/{eid}/start").json()["claimed"] is False


def test_sc4_escalate_then_human_run():
    exc = _break("sc4")
    eid, prep, dec = _flow(exc, lambda s: [("get_pricing_rules", {"order_id": s["order_id"]})])
    assert prep["containment"] and dec["auto"] is False
    assert "needs a decision" in dec["notify_text"] and dec["dashboard_url"].endswith("/ui/")
    e = api.get(f"/exceptions/{eid}").json()
    assert e["status"] == "awaiting_approval" and e["order"]["status"] == "on_hold"
    # what n8n does after the dashboard resumes it with verdict=approved
    top = e["actions"][0]
    out = api.post(f"/n8n/actions/{top['id']}/run", json={"by": "Ops lead", "steps": None}).json()
    assert out["status"] == "resolved"
    assert api.get(f"/exceptions/{eid}").json()["resolution"]["resolved_by"] == "Ops lead"


def test_invalid_input_is_rejected_not_crashing():
    exc = _break("sc1")
    api.post(f"/n8n/exceptions/{exc['id']}/start")
    assert api.post(f"/n8n/exceptions/{exc['id']}/tool", json={"tool": "check_inventory", "input": {}}).status_code == 422
    assert api.post(f"/n8n/exceptions/{exc['id']}/tool", json={"tool": "reallocate_stock", "input": {}}).status_code == 422
    bad = api.post(f"/n8n/exceptions/{exc['id']}/prepare", json={"resolution": {"root_cause": ""}}).json()
    assert bad["valid"] is False and bad["errors"]
