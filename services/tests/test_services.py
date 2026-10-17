"""End-to-end checks for the mock systems (owner: P1).   Run: make test-services

Each scenario test walks the path the agent should take: trigger → investigate → act,
so if these pass, P2's agent has everything it needs from the data layer.
"""
import os
import tempfile

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from services.app import app  # noqa: E402

c = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    assert c.post("/admin/reset").json()["ok"]


def test_seed_is_healthy():
    orders = c.get("/orders").json()
    assert len(orders) == 50
    bad = {"pick_failed", "payment_failed", "on_hold"}
    assert not [o for o in orders if o["status"] in bad]
    for o in orders:
        if o["status"] not in ("shipped", "delivered"):
            assert not c.get(f"/sla/{o['id']}").json()["at_risk"], o["id"]
        assert not c.get(f"/pricing/order/{o['id']}").json()["violations"], o["id"]
    assert c.get("/admin/scenarios").json()


def test_fail_flags():
    assert c.get("/orders/ORD-1001?fail=error").status_code == 503
    assert c.get("/orders/NOPE").status_code == 404
    assert c.post("/admin/trigger?fail=nope").status_code == 422


def test_sc1_phantom_stock():
    c.post("/admin/trigger?fail=sc1")
    o = c.get("/orders/ORD-1001").json()
    assert o["status"] == "pick_failed" and o["warehouse_id"] == "WH-A"
    inv = {l["warehouse_id"]: l for l in c.get("/inventory/SKU-HDPH").json()["locations"]}
    assert inv["WH-A"]["on_hand"] == 5 and inv["WH-A"]["last_scan_qty"] == 0 and inv["WH-A"]["scan_mismatch"]
    assert inv["WH-B"]["verified_available"] >= 2
    assert inv["WH-C"]["verified_available"] < 2
    # wrong move fails loudly
    assert not c.post("/actions/reallocate_stock", json={"order_id": "ORD-1001", "from": "WH-A", "to": "WH-C"}).json()["ok"]
    r = c.post("/actions/reallocate_stock", json={"order_id": "ORD-1001", "from": "WH-A", "to": "WH-B"}).json()
    assert r["ok"] and r["cost"] == 120 and r["meets_promise"]
    assert c.get("/orders/ORD-1001").json()["status"] == "allocated"
    assert c.get("/cases/similar?domain=inventory&symptom=pick failed item not found").json()["cases"][0]["similarity"] > 0


def test_sc2_supplier_delay():
    assert not c.get("/sla/ORD-1002").json()["at_risk"]
    c.post("/admin/trigger?fail=sc2")
    sla = c.get("/sla/ORD-1002").json()
    assert sla["at_risk"] and sla["blocking_po"]["id"] == "PO-2001"
    assert not any(x["meets_promise"] for x in sla["carrier_options"])  # expediting alone can't save it
    po = c.get("/suppliers/status?order_id=ORD-1002").json()["purchase_orders"][0]
    assert po["is_delayed"]
    alt = po["alternates"][0]
    assert alt["supplier_id"] == "SUP-3" and alt["extra_cost_vs_current"] == 2000
    r = c.post("/actions/switch_supplier", json={"po_id": "PO-2001", "supplier_id": "SUP-3"}).json()
    assert r["ok"] and r["cost"] == 2000 and not r["meets_promise"]  # standard shipping still late
    r = c.post("/actions/expedite_shipping", json={"order_id": "ORD-1002", "carrier": "EXPRESS"}).json()
    assert r["ok"] and r["cost"] == 350 and r["meets_promise"]


def test_sc3_expired():
    c.post("/admin/trigger?fail=sc3_expired")
    p = c.get("/payments/ORD-1003").json()
    assert p["order_status"] == "payment_failed"
    assert p["risk_signals"]["card_expired"] and p["risk_signals"]["max_fraud_score"] < 0.3
    assert not c.post("/actions/retry_payment", json={"order_id": "ORD-1003"}).json()["ok"]
    assert c.post("/actions/notify_customer", json={"order_id": "ORD-1003", "message": "Please update card"}).json()["ok"]


def test_sc3_fraud():
    c.post("/admin/trigger?fail=sc3_fraud")
    s = c.get("/payments/ORD-1003").json()["risk_signals"]
    assert not s["card_expired"] and s["ip_billing_country_mismatch"]
    assert s["max_fraud_score"] > 0.8 and s["decline_count"] == 3 and s["declines_within_minutes"] <= 5
    assert not c.post("/actions/retry_payment", json={"order_id": "ORD-1003"}).json()["ok"]
    assert c.post("/actions/hold_order", json={"order_id": "ORD-1003", "reason": "fraud_review"}).json()["ok"]
    assert c.get("/orders/ORD-1003").json()["status"] == "on_hold"


def test_sc3_healthy_retry_captures():
    r = c.post("/actions/retry_payment", json={"order_id": "ORD-1003"}).json()
    assert r["ok"]


def test_sc4_pricing():
    c.post("/admin/trigger?fail=sc4")
    p = c.get("/pricing/order/ORD-1004").json()
    rules = {v["rule"] for v in p["violations"]}
    assert {"non_stackable_combined", "below_margin_floor"} <= rules
    assert p["charged_total"] == 12349.35 and p["price_as_applied"] < p["total_cost"]
    assert p["price_by_rules"] == 15199.2 and p["codes_by_rules"] == ["FEST20"]
    assert not c.post("/actions/apply_price_correction", json={"order_id": "ORD-1004", "price": 12000}).json()["ok"]
    r = c.post("/actions/apply_price_correction",
               json={"order_id": "ORD-1004", "price": 15199.2, "promo_codes": ["FEST20"]}).json()
    assert r["ok"] and r["revenue_recovered"] == 2849.85
    assert not c.get("/pricing/order/ORD-1004").json()["violations"]
    # SKU-level endpoint (tool contract: sku + promo codes)
    assert c.get("/pricing/SKU-MNTR?promo_codes=FEST20,NEWUSER15").json()["violations"]


def test_action_log_and_reset():
    c.post("/actions/hold_order", json={"order_id": "ORD-1001"})
    assert c.get("/admin/action_log").json()[0]["action_type"] == "hold_order"
    c.post("/admin/reset")
    assert c.get("/admin/action_log").json() == []
    assert c.get("/orders/ORD-1001").json()["status"] == "allocated"


def test_undo_reverses_and_refuses_safely():  # FR-15
    c.post("/admin/trigger?fail=sc1")
    reserved = lambda wh: next(l for l in c.get("/inventory/SKU-HDPH").json()["locations"] if l["warehouse_id"] == wh)["reserved"]  # noqa: E731
    before = {wh: reserved(wh) for wh in ("WH-A", "WH-B")}
    r = c.post("/actions/reallocate_stock", json={"order_id": "ORD-1001", "from": "WH-A", "to": "WH-B"}).json()
    assert r["log_id"] and r["previous_status"] == "pick_failed"
    u = c.post("/actions/undo", json={"log_id": r["log_id"], "reason": "test"}).json()
    assert u["ok"] and u["cost"] == -120 and u["restored_warehouse"] == "WH-A"
    o = c.get("/orders/ORD-1001").json()
    assert o["status"] == "pick_failed" and o["warehouse_id"] == "WH-A"
    assert {wh: reserved(wh) for wh in ("WH-A", "WH-B")} == before
    assert c.post("/actions/undo", json={"log_id": r["log_id"]}).json()["message"] == "Already undone"

    e = c.post("/actions/expedite_shipping", json={"order_id": "ORD-1001", "carrier": "EXPRESS"}).json()
    assert c.post("/actions/undo", json={"log_id": e["log_id"]}).json()["restored_carrier"] == e["previous_carrier"]
    h = c.post("/actions/hold_order", json={"order_id": "ORD-1001"}).json()
    assert c.post("/actions/undo", json={"log_id": h["log_id"]}).json()["restored_status"] == "pick_failed"

    n = c.post("/actions/notify_customer", json={"order_id": "ORD-1001", "message": "hi"}).json()
    u = c.post("/actions/undo", json={"log_id": n["log_id"]}).json()
    assert not u["ok"] and "already been sent" in u["message"]
    assert c.post("/actions/undo", json={"log_id": 999999}).status_code == 404


def test_sc5_phantom_stock_tight_deadline():
    c.post("/admin/trigger?fail=sc5")
    o = c.get("/orders/ORD-1001").json()
    assert o["status"] == "pick_failed"
    sla = c.get("/sla/ORD-1001").json()
    assert sla["at_risk"] and 60 < sla["hours_left"] < 80
    meets = {x["carrier"]: x["meets_promise"] for x in sla["carrier_options"]}
    assert meets == {"STANDARD": False, "EXPRESS": True, "NEXT_DAY": True}
