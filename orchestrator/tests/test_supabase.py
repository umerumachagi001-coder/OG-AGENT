"""Supabase integration: login protection and the copy of case records (no network: Supabase is simulated)."""
import json
import os
import tempfile
import time

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "orch_test.db"))
os.environ.setdefault("OEA_DISABLE_LOOP", "1")
os.environ.setdefault("OEA_AGENT_MODE", "rules")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.tools import Services  # noqa: E402
from orchestrator import supabase_integration as sb  # noqa: E402
from orchestrator.app import State, app as orch_app  # noqa: E402
from services.app import app as services_app  # noqa: E402

services_http = TestClient(services_app)
State.services_factory = lambda: Services(services_http)
api = TestClient(orch_app)

URL = "https://example-project.supabase.co"
GOOD = "token-for-asha"


class FakeSupabase:
    """Just enough of Supabase Auth + PostgREST: checks the headers the real service requires."""

    def __init__(self):
        self.tables: dict[str, dict[tuple, dict]] = {}
        self.calls = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if req.url.path == "/auth/v1/user":
            assert req.headers["apikey"] == "sb_publishable_test"
            if req.headers.get("authorization") == f"Bearer {GOOD}":
                return httpx.Response(200, json={"id": "u1", "email": "asha@example.com",
                                                 "user_metadata": {"full_name": "Asha Rao"}})
            return httpx.Response(401, json={"msg": "invalid JWT"})
        assert req.url.path.startswith("/rest/v1/")
        assert req.headers["apikey"] == "sb_secret_test" and "authorization" not in req.headers
        table = req.url.path.rsplit("/", 1)[1]
        rows = self.tables.setdefault(table, {})
        if req.method == "POST":
            assert "resolution=merge-duplicates" in req.headers["prefer"]
            keys = req.url.params.get("on_conflict", "id").split(",")
            for row in json.loads(req.content):
                rows[tuple(row[k] for k in keys)] = row
            return httpx.Response(201)
        if req.method == "DELETE":
            eq = {k: v[3:] for k, v in req.url.params.items()}
            for k in [k for k, r in rows.items() if all(str(r[f]) == v for f, v in eq.items())]:
                del rows[k]
            return httpx.Response(204)
        return httpx.Response(405)


@pytest.fixture
def supa(monkeypatch):
    fake = FakeSupabase()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setattr(sb, "auth_client", httpx.Client(transport=transport))
    sb._token_cache.clear()
    sync = sb.SupabaseSync(http=httpx.Client(transport=transport, base_url=f"{URL}/rest/v1",
                                             headers={"apikey": "sb_secret_test", "Content-Type": "application/json"}))
    api.post("/reset", headers={"Authorization": f"Bearer {GOOD}"})
    yield fake, sync
    sb._token_cache.clear()


def wait_done(eid, headers):
    for _ in range(200):
        e = api.get(f"/exceptions/{eid}", headers=headers).json()
        if e["status"] in ("auto_resolved", "awaiting_approval") and not e["investigating"]:
            return e
        time.sleep(0.05)
    raise AssertionError("stuck")


def test_off_by_default():
    for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"):
        assert not os.environ.get(k)
    api.post("/reset")
    assert api.get("/auth/config").json() == {"auth_enabled": False, "url": None, "publishable_key": None}
    assert api.get("/exceptions").status_code == 200                     # no login needed


def test_login_protects_the_dashboard_api(supa):
    cfg = api.get("/auth/config").json()
    assert cfg == {"auth_enabled": True, "url": URL, "publishable_key": "sb_publishable_test"}
    assert "secret" not in json.dumps(cfg)                               # the secret key never leaves the backend
    assert api.get("/health").status_code == 200                         # public
    assert api.get("/ui/").status_code in (200, 404)                     # public (404 when the UI isn't built)
    assert api.get("/exceptions").status_code == 401
    assert api.post("/trigger?fail=sc1").status_code == 401
    assert api.get("/exceptions", headers={"Authorization": "Bearer forged"}).status_code == 401
    me = api.get("/auth/me", headers={"Authorization": f"Bearer {GOOD}"}).json()
    assert me["user"] == {"id": "u1", "email": "asha@example.com", "name": "Asha Rao"}


def test_decisions_are_recorded_against_the_signed_in_person(supa):
    h = {"Authorization": f"Bearer {GOOD}"}
    eid = api.post("/trigger?fail=sc2", headers=h).json()["exceptions_created"][0]
    e = wait_done(eid, h)
    out = api.post(f"/actions/{e['actions'][0]['id']}/approve", json={"by": "Someone Else"}, headers=h).json()
    assert out["status"] == "resolved"
    assert api.get(f"/exceptions/{eid}", headers=h).json()["resolution"]["resolved_by"] == "Asha Rao"


def test_case_records_are_copied_to_supabase(supa):
    fake, sync = supa
    h = {"Authorization": f"Bearer {GOOD}"}
    eid = api.post("/trigger?fail=sc5", headers=h).json()["exceptions_created"][0]
    wait_done(eid, h)
    sent = sync.tick()
    run = sync.run_id
    assert list(fake.tables["oea_runs"]) == [(run,)]
    exc = fake.tables["oea_exceptions"][(run, eid)]
    assert exc["status"] == "auto_resolved" and exc["order_id"] == "ORD-1001"
    steps = [r for r in fake.tables["oea_investigation_steps"].values() if r["exception_id"] == eid]
    assert steps and isinstance(steps[0]["content"], dict)                # JSON stored as JSON, not text
    acts = [r for r in fake.tables["oea_proposed_actions"].values() if r["exception_id"] == eid]
    assert acts[0]["requires_approval"] is False and acts[0]["params"]["steps"]
    assert (run, eid) in fake.tables["oea_resolutions"]
    assert any(r["action_type"] == "expedite_shipping" for r in fake.tables["oea_action_log"].values())
    assert sent["oea_exceptions"] >= 13                                  # 12 seeded history cases + this one

    assert sync.tick() == {}                                             # nothing changed: nothing sent

    api.post(f"/exceptions/{eid}/undo", headers=h)                       # undo withdraws the resolution
    sent = sync.tick()
    assert fake.tables["oea_exceptions"][(run, eid)]["status"] == "awaiting_approval"
    assert (run, eid) not in fake.tables["oea_resolutions"]
    assert sent["oea_exceptions"] == 1 and sent["oea_investigation_steps"] >= 3

    api.post("/reset", headers=h)                                        # a reset starts a new run
    sync.tick()
    assert len(fake.tables["oea_runs"]) == 2 and sync.run_id != run
    assert fake.tables["oea_exceptions"][(run, eid)]["status"] == "awaiting_approval"   # old run kept


def test_sync_endpoints(supa, monkeypatch):
    h = {"Authorization": f"Bearer {GOOD}"}
    st = api.get("/supabase/status", headers=h).json()
    assert st["enabled"] and st["auth_enabled"] and st["project"] == URL
    monkeypatch.delenv("SUPABASE_SECRET_KEY")
    assert api.post("/supabase/sync", headers=h).status_code == 409
