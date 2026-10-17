"""Orchestrator API (owner: P4) — the one backend the dashboard talks to.  Port 8001.

    python -m uvicorn orchestrator.app:app --port 8001       # or: make demo (starts everything)

Background loop (every OEA_DETECT_INTERVAL s): detector → new exceptions → agent investigates
automatically (OEA_AUTO_INVESTIGATE=1). So in the demo: click "Trigger failure" → within ~2 s the
exception appears and its trail starts filling.

Engine (OEA_ENGINE):
    python  the built-in loop detects and investigates (agent/runner.py)
    n8n     n8n workflows in n8n/workflows/ run the whole process through the /n8n bridge
            (orchestrator/n8n_bridge.py); this app only serves the dashboard and resumes n8n on approval

Endpoints (all JSON):
    GET  /exceptions?status=open,awaiting_approval     queue, sorted by severity then newest (FR-16)
    GET  /exceptions/{id}                               exception + order + actions + resolution (FR-17)
    GET  /exceptions/{id}/trail?after=<step_no>         trail steps after a step (poll every 1 s)
    POST /exceptions/{id}/investigate                   (re)run the agent — 202, runs in background
    POST /actions/{id}/approve   {"by": "...", "steps": [...]?}   (FR-13, steps = modify)
    POST /actions/{id}/reject    {"by": "...", "feedback": "..."}
    POST /exceptions/{id}/undo   {"by": "...", "reason": "..."}   reverse an automatic fix (FR-15)
    /auth/*, /supabase/*                                Supabase sign-in + record copy (orchestrator/auth_api.py)
    POST /detect                                        run the detector now
    GET  /metrics                                       FR-19 numbers for the metrics strip
    GET  /scenarios · POST /trigger?fail=sc1 · POST /reset · GET /orders   (proxied to services)
    GET  /health
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

import httpx

from agent import runner, store
from agent.tools import Services, ToolError, validate_step
from orchestrator import auth_api, n8n_bridge
from orchestrator import supabase_integration as supabase
from services.detector.rules import detect_once

log = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per internal request is noise

DETECT_INTERVAL = float(os.environ.get("OEA_DETECT_INTERVAL", "2"))
AUTO_INVESTIGATE = os.environ.get("OEA_AUTO_INVESTIGATE", "1") == "1"
MANUAL_MINUTES_PER_EXCEPTION = 20   # pitch baseline: a human spends ~20 min per exception
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ENGINE = os.environ.get("OEA_ENGINE", "python").lower()          # python | n8n
N8N_URL = os.environ.get("OEA_N8N_URL", "http://127.0.0.1:5678").rstrip("/")


class State:
    services_factory = Services          # tests swap this for a TestClient-backed Services
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="agent")
    in_flight: set[int] = set()
    lock = threading.Lock()
    stop = threading.Event()
    paused = threading.Event()           # set during /reset so the loop doesn't race the DB rebuild


_svc: Services | None = None


def svc() -> Services:
    """One shared client (connection pooling) instead of a new TCP connection per request."""
    global _svc
    if _svc is None or getattr(_svc, "_factory", None) is not State.services_factory:
        _svc = State.services_factory()
        _svc._factory = State.services_factory
    return _svc


def _investigate_bg(exc_id: int):
    with State.lock:
        if exc_id in State.in_flight:
            return False
        State.in_flight.add(exc_id)

    def job():
        try:
            r = runner.investigate(exc_id, svc=svc())
            log.info("exception %s → %s", exc_id, r.get("status"))
        except Exception:  # noqa: BLE001
            log.exception("investigation %s crashed", exc_id)
            try:
                store.set_status(exc_id, "failed")
            except Exception:  # noqa: BLE001
                pass
        finally:
            with State.lock:
                State.in_flight.discard(exc_id)

    State.pool.submit(job)
    return True


def _n8n(path: str, payload: dict, timeout: float = 20) -> dict:
    """Call an n8n webhook (production URL). Raises HTTPException if n8n is down or the workflow is inactive."""
    try:
        r = httpx.post(f"{N8N_URL}/webhook/{path}", json=payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"n8n unreachable at {N8N_URL}: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(502, f"n8n webhook {path} answered {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}


def _resume_n8n(exc_id: int, payload: dict) -> bool:
    """Resume the paused n8n execution for this exception. False if there is none or n8n is unreachable."""
    url = n8n_bridge.pop_resume_url(exc_id)
    if not url:
        return False
    try:
        r = httpx.post(url, json=payload, timeout=30)
        if r.status_code < 400:
            return True
        log.warning("n8n resume %s → %s %s", exc_id, r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log.warning("n8n resume %s failed: %s", exc_id, e)
    return False


def _loop():
    if ENGINE == "n8n":
        return   # n8n's schedule trigger does detection
    while not State.stop.wait(DETECT_INTERVAL):
        if State.paused.is_set():
            continue
        try:
            for e in detect_once():
                log.info("detected #%s %s %s (%s)", e["id"], e["order_id"], e["domain"], e["severity"])
                if AUTO_INVESTIGATE:
                    _investigate_bg(e["id"])
        except Exception as e:  # noqa: BLE001 — never let the loop die mid-demo
            log.warning("detector loop: %s", e)


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("OEA_DISABLE_LOOP") != "1":
        State.stop.clear()
        threading.Thread(target=_loop, daemon=True, name="detector").start()
    sb = supabase.settings()
    if sb["sync_enabled"]:
        supabase.sync.paused = State.paused.is_set     # don't copy while Reset demo rebuilds the database
        supabase.sync.start()
        log.info("copying case records to Supabase (%s)", sb["url"])
    if sb["auth_enabled"]:
        log.info("dashboard login required (Supabase Auth)")
    yield
    State.stop.set()
    supabase.sync.stop()


app = FastAPI(title="Order Exception Agent — orchestrator", version="0.1.0", lifespan=lifespan)
# Login check first, CORS added after so it wraps it: a 401 still carries CORS headers for the browser.
app.middleware("http")(auth_api.require_login)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(n8n_bridge.router)
app.include_router(auth_api.router)
n8n_bridge.svc = lambda: svc()
n8n_bridge.is_paused = lambda: State.paused.is_set()


# ───────── queue & detail ─────────

def _rows(sql, args=()):
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@app.get("/exceptions")
def list_exceptions(status: str | None = None, include_history: bool = False, limit: int = 100):
    q = ("SELECT e.*, o.total AS order_value, o.status AS order_status, cu.name AS customer_name, cu.tier AS customer_tier,"
         " (SELECT COUNT(*) FROM proposed_actions pa WHERE pa.exception_id=e.id AND pa.status='proposed'"
         "  AND pa.requires_approval=1) AS pending_approvals"
         " FROM exceptions e JOIN orders o ON o.id=e.order_id JOIN customers cu ON cu.id=o.customer_id")
    where, args = [], []
    if status:
        sts = status.split(",")
        where.append(f"e.status IN ({','.join('?' * len(sts))})")
        args += sts
    if not include_history:
        # seeded history rows are for metrics/similar cases, not the live queue
        where.append("e.origin = 'live'")
    if where:
        q += " WHERE " + " AND ".join(where)
    out = _rows(q, args)
    out.sort(key=lambda e: (e["status"] in ("auto_resolved", "resolved", "rejected"),
                            SEVERITY_RANK.get(e["severity"], 9), -e["id"]))
    return out[:limit]


@app.get("/exceptions/{exc_id}")
def get_exception(exc_id: int):
    e = store.get_exception(exc_id)
    if not e:
        raise HTTPException(404, "Exception not found")
    try:
        e["order"] = svc().call("get_order", {"order_id": e["order_id"]})
    except ToolError:
        e["order"] = None
    e["actions"] = store.get_actions(exc_id)
    for a in e["actions"]:
        a.pop("params_json", None)
    res = _rows("SELECT * FROM resolutions WHERE exception_id=?", (exc_id,))
    e["resolution"] = res[0] if res else None
    diag = [s["content"] for s in store.get_trail(exc_id) if s["type"] == "decision" and s["content"].get("kind") == "diagnosis"]
    e["evidence"] = diag[-1].get("evidence", []) if diag else []
    e["customer_message"] = diag[-1].get("customer_message") if diag else None
    e["investigating"] = exc_id in State.in_flight
    e["undo"] = runner.undo_plan(exc_id)
    return e


@app.get("/exceptions/{exc_id}/trail")
def get_trail(exc_id: int, after: int = 0):
    return [s for s in store.get_trail(exc_id) if s["step_no"] > after]


@app.post("/exceptions/{exc_id}/investigate", status_code=202)
def investigate(exc_id: int):
    e = store.get_exception(exc_id)
    if not e:
        raise HTTPException(404, "Exception not found")
    if e["status"] not in ("open", "failed"):
        raise HTTPException(409, f"Exception is {e['status']}")
    if ENGINE == "n8n":
        _n8n("oea-investigate", {"id": exc_id})
        return {"queued": True, "exception_id": exc_id, "engine": "n8n"}
    return {"queued": _investigate_bg(exc_id), "exception_id": exc_id}


# ───────── human in the loop ─────────

class ApproveBody(BaseModel):
    by: str = "human"
    steps: list[dict] | None = None


class RejectBody(BaseModel):
    by: str = "human"
    feedback: str | None = None


@app.post("/actions/{action_id}/approve")
def approve(action_id: int, request: Request, body: ApproveBody | None = None):
    body = body or ApproveBody()
    body.by = auth_api.acting_user(request, body.by)
    a = store.get_action(action_id)
    if not a:
        raise HTTPException(422, f"Action {action_id} not found")
    if body.steps is not None:   # edited steps are checked here, before n8n is resumed with them
        if not body.steps:
            raise HTTPException(422, "No steps to run: an edited option needs at least one step")
        errs = [e for e in (validate_step(s) for s in body.steps) if e]
        if errs:
            raise HTTPException(422, f"Invalid steps: {errs}")
    if ENGINE == "n8n" and a["status"] in ("proposed", "failed"):
        # Hand the decision back to the paused n8n execution; it runs the fix and records the result.
        if _resume_n8n(a["exception_id"], {"verdict": "approved", "action_id": action_id, "by": body.by,
                                           "steps": body.steps}):
            return {"exception_id": a["exception_id"], "action_id": action_id, "status": "resuming", "engine": "n8n"}
        log.warning("no paused n8n run for exception %s; executing directly", a["exception_id"])
    try:
        return runner.approve(action_id, by=body.by, steps=body.steps, svc=svc())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/actions/{action_id}/reject")
def reject(action_id: int, request: Request, body: RejectBody | None = None):
    body = body or RejectBody()
    body.by = auth_api.acting_user(request, body.by)
    try:
        out = runner.reject(action_id, by=body.by, feedback=body.feedback)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if ENGINE == "n8n" and not out.get("skipped") and out["remaining_options"] == 0:
        _resume_n8n(out["exception_id"], {"verdict": "closed", "by": body.by, "feedback": body.feedback})
    return out


class UndoBody(BaseModel):
    by: str = "human"
    reason: str | None = None


@app.post("/exceptions/{exc_id}/undo")
def undo(exc_id: int, request: Request, body: UndoBody | None = None):
    """Reverse an automatic fix and hand the case to a person (FR-15). The n8n run for an automatic fix
    has already finished, so this runs directly in both engines; the trail records it the same way."""
    body = body or UndoBody()
    body.by = auth_api.acting_user(request, body.by)
    if not store.get_exception(exc_id):
        raise HTTPException(404, "Exception not found")
    try:
        return runner.undo(exc_id, by=body.by, reason=body.reason, svc=svc())
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


# ───────── detector & metrics ─────────

@app.post("/detect")
def detect(investigate: bool = True):
    if ENGINE == "n8n":
        return _n8n("oea-scan", {}).get("created", [])
    created = detect_once()
    if investigate:
        for e in created:
            _investigate_bg(e["id"])
    return created


@app.get("/metrics")
def metrics():
    rows = _rows(
        "SELECT e.id, e.status, e.created_at, e.resolved_at, o.total, r.resolved_by, r.minutes_to_resolve,"
        " (SELECT COALESCE(SUM(cost),0) FROM proposed_actions pa WHERE pa.exception_id=e.id AND pa.status='executed') AS cost"
        " FROM exceptions e JOIN orders o ON o.id=e.order_id LEFT JOIN resolutions r ON r.exception_id=e.id")
    done = [r for r in rows if r["status"] in ("auto_resolved", "resolved")]
    auto = [r for r in rows if r["status"] == "auto_resolved"]
    human = [r for r in done if r["status"] == "resolved"]
    mins = lambda rs: round(sum(r["minutes_to_resolve"] or 0 for r in rs) / len(rs), 1) if rs else None  # noqa: E731
    return {
        "exceptions_total": len(rows),
        "open": sum(r["status"] in ("open", "investigating") for r in rows),
        "awaiting_approval": sum(r["status"] == "awaiting_approval" for r in rows),
        "resolved": len(done),
        "auto_resolved": len(auto),
        "auto_resolved_pct": round(100 * len(auto) / len(done), 1) if done else 0,
        "avg_minutes_auto": mins(auto),
        "avg_minutes_human": mins(human),
        "analyst_minutes_saved": round(len(auto) * MANUAL_MINUTES_PER_EXCEPTION - sum(r["minutes_to_resolve"] or 0 for r in auto), 1),
        "revenue_protected": round(sum(r["total"] for r in done), 2),
        "action_cost_spent": round(sum(r["cost"] for r in done), 2),
        "manual_baseline_minutes": MANUAL_MINUTES_PER_EXCEPTION,
    }


# ───────── demo controls (proxy to services so the dashboard needs one URL) ─────────

def _proxy(method: str, path: str, **kw):
    s = svc()
    try:
        r = getattr(s.http, method)(path, **kw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"services unreachable: {e}") from e
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    return r.json()


@app.get("/scenarios")
def scenarios():
    return _proxy("get", "/admin/scenarios")


@app.post("/trigger")
def trigger(fail: str = Query(...)):
    out = _proxy("post", "/admin/trigger", params={"fail": fail})
    if ENGINE == "n8n":
        # Run n8n's detector workflow now instead of waiting for its schedule.
        try:
            created = _n8n("oea-scan", {}).get("created", [])
        except HTTPException as e:
            out["n8n_error"] = e.detail
            created = []
        out["exceptions_created"] = [e["id"] for e in created]
        return out
    created = detect_once() if not State.paused.is_set() else []   # don't wait for the next tick
    for e in created:
        if AUTO_INVESTIGATE:
            _investigate_bg(e["id"])
    out["exceptions_created"] = [e["id"] for e in created]
    return out


@app.post("/reset")
def reset():
    State.paused.set()
    if ENGINE == "n8n":
        # Close n8n runs still waiting for a decision, so a reset never leaves orphaned executions.
        for exc_id, _url in n8n_bridge.all_resume_urls():
            _resume_n8n(exc_id, {"verdict": "closed", "by": "demo reset"})
    try:
        out = _proxy("post", "/admin/reset")
    finally:
        State.paused.clear()
    return out


@app.get("/orders")
def orders(status: str | None = None, limit: int = 100):
    return _proxy("get", "/orders", params={k: v for k, v in {"status": status, "limit": limit}.items() if v})


@app.get("/health")
def health():
    try:
        s = _proxy("get", "/health")
    except HTTPException as e:
        return {"ok": False, "services": str(e.detail)}
    sb = supabase.settings()
    out = {"ok": True, "services": s, "auto_investigate": AUTO_INVESTIGATE, "agent_mode": runner._mode(None),
           "engine": ENGINE, "in_flight": sorted(State.in_flight),
           "supabase": {"auth": sb["auth_enabled"], "sync": sb["sync_enabled"],
                        "last_ok": supabase.sync.status["last_ok"], "error": supabase.sync.status["last_error"]}}
    if ENGINE == "n8n":
        try:
            out["n8n"] = httpx.get(f"{N8N_URL}/healthz", timeout=3).status_code == 200
        except httpx.HTTPError:
            out["n8n"] = False
        out["n8n_editor"] = N8N_URL
    return out


# ───────── built dashboard (P3) at /ui, so `make demo` is one command ─────────

_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if _DIST.exists():
    app.mount("/ui", StaticFiles(directory=_DIST, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/" if _DIST.exists() else "/docs")
