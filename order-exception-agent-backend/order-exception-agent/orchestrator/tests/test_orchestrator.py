"""Full-stack tests: dashboard-facing API → detector → agent → services (owner: P4).  Run: make test-orchestrator"""
import os
import tempfile
import time

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "orch_test.db"))
os.environ.setdefault("OEA_DISABLE_LOOP", "1")
os.environ.setdefault("OEA_AGENT_MODE", "rules")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.tools import Services  # noqa: E402
from orchestrator.app import State, app as orch_app  # noqa: E402
from services.app import app as services_app  # noqa: E402

services_http = TestClient(services_app)
State.services_factory = lambda: Services(services_http)
api = TestClient(orch_app)


@pytest.fixture(autouse=True)
def clean():
    assert api.post("/reset").status_code == 200


def wait_status(exc_id, statuses=("auto_resolved", "awaiting_approval", "failed"), timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        e = api.get(f"/exceptions/{exc_id}").json()
        if e["status"] in statuses and not e["investigating"]:
            return e
        time.sleep(0.05)
    raise AssertionError(f"exception {exc_id} stuck in {e['status']}")


def test_calm_state():
    assert api.get("/exceptions").json() == []                         # history hidden from the queue
    assert len(api.get("/exceptions?include_history=true").json()) == 12
    assert api.post("/detect").json() == []
    m = api.get("/metrics").json()
    assert m["resolved"] == 12 and m["open"] == 0
    assert api.get("/health").json()["ok"]


def test_sc1_trigger_to_auto_resolved():
    r = api.post("/trigger?fail=sc1").json()
    assert len(r["exceptions_created"]) == 1
    eid = r["exceptions_created"][0]
    e = wait_status(eid)
    assert e["status"] == "auto_resolved" and e["severity"] == "medium" and e["domain"] == "inventory"
    assert e["order"]["warehouse_id"] == "WH-B" and e["evidence"] and e["resolution"]["resolved_by"] == "agent"
    trail = api.get(f"/exceptions/{eid}/trail").json()
    assert api.get(f"/exceptions/{eid}/trail?after={trail[-2]['step_no']}").json() == trail[-1:]
    assert api.post("/detect").json() == []                             # no duplicate after resolution
    m = api.get("/metrics").json()
    assert m["auto_resolved"] >= 1 and m["analyst_minutes_saved"] > 0


def test_all_scenarios_queue_and_human_loop():
    ids = {}
    for s in ("sc1", "sc2", "sc3_expired", "sc4"):
        ids[s] = api.post(f"/trigger?fail={s}").json()["exceptions_created"][0]
    for eid in ids.values():
        wait_status(eid)
    q = api.get("/exceptions").json()
    assert [e["status"] for e in q[:2]] == ["awaiting_approval"] * 2     # open work first
    assert q[0]["severity"] == "high" and q[0]["domain"] == "supplier"   # then by severity
    assert {e["id"] for e in q if e["status"] == "auto_resolved"} == {ids["sc1"], ids["sc3_expired"]}

    sc2 = api.get(f"/exceptions/{ids['sc2']}").json()
    top = sc2["actions"][0]
    assert top["requires_approval"] == 1 and top["params"]["policy"]["reasons"]
    out = api.post(f"/actions/{top['id']}/approve", json={"by": "Ops: Demo"}).json()
    assert out["status"] == "resolved"
    assert api.get(f"/exceptions/{ids['sc2']}").json()["resolution"]["resolved_by"] == "Ops: Demo"

    sc4 = api.get(f"/exceptions/{ids['sc4']}").json()
    assert sc4["order"]["status"] == "on_hold" and sc4["customer_message"]
    api.post(f"/actions/{sc4['actions'][1]['id']}/reject", json={"by": "Ops", "feedback": "No"})
    # modify: approve option 1 with a different (still valid) price
    steps = sc4["actions"][0]["params"]["steps"]
    steps[0]["params"]["price"] = 15500
    out = api.post(f"/actions/{sc4['actions'][0]['id']}/approve", json={"by": "Ops", "steps": steps}).json()
    assert out["status"] == "resolved"
    assert api.get("/orders?status=payment_pending").json()[0]["total"] == 15500


def test_reset_clears_live_exceptions():
    api.post("/trigger?fail=sc1")
    time.sleep(0.5)
    api.post("/reset")
    assert api.get("/exceptions").json() == []


def test_unknown_scenario():
    assert api.post("/trigger?fail=nope").status_code == 422
