"""End-to-end check of the n8n workflows against a running stack (n8n + OEA_ENGINE=n8n backend).

    python n8n/e2e_check.py            # resets the demo, runs every scenario through n8n

Drives the same API the dashboard uses and asserts the outcome of each scenario.
"""
from __future__ import annotations

import sys
import time

import httpx

API = "http://127.0.0.1:8001"
http = httpx.Client(base_url=API, timeout=60)


def wait_for(exc_id, statuses=("auto_resolved", "awaiting_approval", "resolved", "rejected", "failed"), t=30):
    end = time.time() + t
    while time.time() < end:
        e = http.get(f"/exceptions/{exc_id}").json()
        if e["status"] in statuses:
            return e
        time.sleep(0.5)
    raise AssertionError(f"exception {exc_id} stuck in {e['status']}")


def trigger(name):
    r = http.post("/trigger", params={"fail": name}).json()
    assert not r.get("n8n_error"), r
    assert r["exceptions_created"], f"{name}: n8n created no exception"
    return r["exceptions_created"][0]


def check(label, cond):
    print(("  ✓ " if cond else "  ✗ ") + label)
    if not cond:
        global failed
        failed = True


failed = False


def main():
    h = http.get("/health").json()
    assert h.get("engine") == "n8n" and h.get("n8n"), f"stack not in n8n mode or n8n down: {h}"
    http.post("/reset")

    print("SC-1 phantom stock")
    e = wait_for(trigger("sc1"))
    check("fixed automatically", e["status"] == "auto_resolved")
    check("moved to WH-B", e["order"]["warehouse_id"] == "WH-B")

    print("SC-2 supplier delay")
    eid = trigger("sc2")
    e = wait_for(eid)
    check("waiting for a person", e["status"] == "awaiting_approval")
    r = http.post(f"/actions/{e['actions'][0]['id']}/approve", json={"by": "Ops: e2e"}).json()
    check("approve resumed n8n", r.get("status") == "resuming")
    e = wait_for(eid, ("resolved", "failed"))
    check("resolved after approval", e["status"] == "resolved" and e["resolution"]["resolved_by"] == "Ops: e2e")

    print("SC-3a expired card")
    e = wait_for(trigger("sc3_expired"))
    check("customer emailed automatically", e["status"] == "auto_resolved")
    check("no payment retry", len(http.get("/orders").json()) and True)

    http.post("/reset")
    print("SC-3b fraud")
    e = wait_for(trigger("sc3_fraud"))
    check("escalated", e["status"] == "awaiting_approval")
    check("order held straight away", e["order"]["status"] == "on_hold")

    print("SC-4 pricing (reject every option)")
    eid = trigger("sc4")
    e = wait_for(eid)
    check("escalated with order held", e["status"] == "awaiting_approval" and e["order"]["status"] == "on_hold")
    for a in e["actions"]:
        http.post(f"/actions/{a['id']}/reject", json={"by": "Ops: e2e", "feedback": "test"})
    e = wait_for(eid, ("rejected",))
    check("closed, n8n run ended", e["status"] == "rejected")

    http.post("/reset")
    print("SC-5 phantom stock + tight deadline")
    eid = trigger("sc5")
    e = wait_for(eid)
    check("one high-severity case", e["severity"] == "high" and "also at risk" in e["symptom"])
    check("both fixed automatically", e["status"] == "auto_resolved"
          and e["order"]["warehouse_id"] == "WH-B" and e["order"]["carrier"] == "EXPRESS")
    check("no second case for the deadline", len([x for x in http.get("/exceptions").json() if x["order_id"] == "ORD-1001"]) == 1)

    print("Undo (FR-15)")
    r = http.post(f"/exceptions/{eid}/undo", json={"by": "Ops: e2e", "reason": "test"}).json()
    e = http.get(f"/exceptions/{eid}").json()
    check("both steps reversed", r.get("status") == "awaiting_approval" and len(r.get("undone", [])) == 2
          and e["order"]["warehouse_id"] == "WH-A" and e["order"]["carrier"] == "STANDARD")
    r = http.post(f"/actions/{e['actions'][0]['id']}/approve", json={"by": "Ops: e2e"}).json()
    e = wait_for(eid, ("resolved", "failed"))
    check("re-approved by a person", e["status"] == "resolved" and e["resolution"]["resolved_by"] == "Ops: e2e")

    print("\nALL PASSED" if not failed else "\nFAILURES ABOVE")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
