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


def test_fraud_is_high_severity():
    eid = api.post("/trigger?fail=sc3_fraud").json()["exceptions_created"][0]
    e = wait_status(eid)                                                 # let the investigation finish
    assert e["severity"] == "high" and e["status"] == "awaiting_approval" and e["order"]["status"] == "on_hold"


def test_undo_auto_fix_hands_case_to_person():  # FR-15
    eid = api.post("/trigger?fail=sc1").json()["exceptions_created"][0]
    e = wait_status(eid)
    assert e["undo"]["possible"] and e["undo"]["steps"][0]["action"] == "reallocate_stock", e["undo"]
    out = api.post(f"/exceptions/{eid}/undo", json={"by": "Ops", "reason": "WH-A recount found stock"}).json()
    assert out["status"] == "awaiting_approval" and out["cost_reversed"] == 120
    e = api.get(f"/exceptions/{eid}").json()
    assert e["order"]["warehouse_id"] == "WH-A" and e["resolution"] is None and not e["undo"]["possible"]
    assert all(a["status"] == "proposed" and a["requires_approval"] == 1 for a in e["actions"])
    kinds = [s["content"].get("kind") for s in api.get(f"/exceptions/{eid}/trail").json() if s["type"] == "decision"]
    assert kinds[-2:] == ["human", "rollback"]
    assert api.post("/detect").json() == []                              # no duplicate case
    assert api.post(f"/exceptions/{eid}/undo").status_code == 409        # only once
    assert api.post(f"/actions/{e['actions'][0]['id']}/approve", json={"by": "Ops"}).json()["status"] == "resolved"

    eid2 = api.post("/trigger?fail=sc3_expired").json()["exceptions_created"][0]
    e2 = wait_status(eid2)
    assert not e2["undo"]["possible"] and "emailed" in e2["undo"]["reason"]
    assert api.post(f"/exceptions/{eid2}/undo").status_code == 409


def test_sc5_one_case_two_fixes_then_undo_both():
    eid = api.post("/trigger?fail=sc5").json()["exceptions_created"][0]
    e = wait_status(eid)
    assert e["severity"] == "high" and e["status"] == "auto_resolved" and e["order"]["carrier"] == "EXPRESS"
    assert [x["order_id"] for x in api.get("/exceptions").json()] == ["ORD-1001"]
    assert [s["action"] for s in e["undo"]["steps"]] == ["expedite_shipping", "reallocate_stock"]   # newest first
    out = api.post(f"/exceptions/{eid}/undo", json={"by": "Ops"}).json()
    assert out["cost_reversed"] == 470 and len(out["undone"]) == 2
    o = api.get(f"/exceptions/{eid}").json()["order"]
    assert (o["warehouse_id"], o["carrier"], o["status"]) == ("WH-A", "STANDARD", "pick_failed")


def test_simultaneous_undos_reverse_once():  # found by the ops-lead walkthrough
    import threading
    eid = api.post("/trigger?fail=sc1").json()["exceptions_created"][0]
    wait_status(eid)
    before = {l["warehouse_id"]: l["reserved"] for l in services_http.get("/inventory/SKU-HDPH").json()["locations"]}
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(api.post(f"/exceptions/{eid}/undo", json={}).status_code)) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(codes) == [200, 409, 409]
    after = {l["warehouse_id"]: l["reserved"] for l in services_http.get("/inventory/SKU-HDPH").json()["locations"]}
    assert after["WH-A"] == before["WH-A"] + 2 and after["WH-B"] == before["WH-B"] - 2


def test_late_reject_does_not_rewrite_a_finished_case():
    eid = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    e = wait_status(eid)
    top = e["actions"][0]["id"]
    assert api.post(f"/actions/{top}/approve", json={"by": "Ops"}).json()["status"] == "resolved"
    out = api.post(f"/actions/{top}/reject", json={"feedback": "too late"}).json()
    assert out["skipped"] == "not pending"
    e = api.get(f"/exceptions/{eid}").json()
    assert e["status"] == "resolved" and e["actions"][0]["status"] == "executed"


def test_edited_steps_are_checked_before_running():
    eid = api.post("/trigger?fail=sc2").json()["exceptions_created"][0]
    a = wait_status(eid)["actions"][0]
    bad = [dict(s, params={k: v for k, v in s["params"].items() if k != "supplier_id"}) for s in a["params"]["steps"]]
    assert api.post(f"/actions/{a['id']}/approve", json={"steps": bad}).status_code == 422
    assert api.post(f"/actions/{a['id']}/approve", json={"steps": []}).status_code == 422
    assert api.get(f"/exceptions/{eid}").json()["actions"][0]["status"] == "proposed"   # nothing ran


def test_failed_option_can_be_corrected_and_retried():
    eid = api.post("/trigger?fail=sc4").json()["exceptions_created"][0]
    a = wait_status(eid)["actions"][0]
    steps = a["params"]["steps"]
    steps[0]["params"]["price"] = 9000                                   # typo: below the margin floor
    assert api.post(f"/actions/{a['id']}/approve", json={"steps": steps}).json()["status"] == "failed"
    steps[0]["params"]["price"] = 15500
    assert api.post(f"/actions/{a['id']}/approve", json={"steps": steps}).json()["status"] == "resolved"
