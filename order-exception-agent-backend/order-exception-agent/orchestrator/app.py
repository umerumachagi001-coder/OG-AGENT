"""Orchestrator API (owner: P4) — the one backend the dashboard talks to.  Port 8001.

    python -m uvicorn orchestrator.app:app --port 8001       # or: make demo (starts everything)

Background loop (every OEA_DETECT_INTERVAL s): detector → new exceptions → agent investigates
automatically (OEA_AUTO_INVESTIGATE=1). So in the demo: click "Trigger failure" → within ~2 s the
exception appears and its trail starts filling.

Endpoints (all JSON):
    GET  /exceptions?status=open,awaiting_approval     queue, sorted by severity then newest (FR-16)
    GET  /exceptions/{id}                               exception + order + actions + resolution (FR-17)
    GET  /exceptions/{id}/trail?after=<step_no>         trail steps after a step (poll every 1 s)
    POST /exceptions/{id}/investigate                   (re)run the agent — 202, runs in background
    POST /actions/{id}/approve   {"by": "...", "steps": [...]?}   (FR-13, steps = modify)
    POST /actions/{id}/reject    {"by": "...", "feedback": "..."}
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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

from agent import runner, store
from agent.tools import Services, ToolError
from services.detector.rules import detect_once

log = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

DETECT_INTERVAL = float(os.environ.get("OEA_DETECT_INTERVAL", "2"))
AUTO_INVESTIGATE = os.environ.get("OEA_AUTO_INVESTIGATE", "1") == "1"
MANUAL_MINUTES_PER_EXCEPTION = 20   # pitch baseline: a human spends ~20 min per exception
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


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


def _loop():
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
    yield
    State.stop.set()


app = FastAPI(title="Order Exception Agent — orchestrator", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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
    return {"queued": _investigate_bg(exc_id), "exception_id": exc_id}


# ───────── human in the loop ─────────

class ApproveBody(BaseModel):
    by: str = "human"
    steps: list[dict] | None = None


class RejectBody(BaseModel):
    by: str = "human"
    feedback: str | None = None


@app.post("/actions/{action_id}/approve")
def approve(action_id: int, body: ApproveBody | None = None):
    body = body or ApproveBody()
    try:
        return runner.approve(action_id, by=body.by, steps=body.steps, svc=svc())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/actions/{action_id}/reject")
def reject(action_id: int, body: RejectBody | None = None):
    body = body or RejectBody()
    try:
        return runner.reject(action_id, by=body.by, feedback=body.feedback)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


# ───────── detector & metrics ─────────

@app.post("/detect")
def detect(investigate: bool = True):
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
    created = detect_once() if not State.paused.is_set() else []   # don't wait for the next tick
    for e in created:
        if AUTO_INVESTIGATE:
            _investigate_bg(e["id"])
    out["exceptions_created"] = [e["id"] for e in created]
    return out


@app.post("/reset")
def reset():
    State.paused.set()
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
    return {"ok": True, "services": s, "auto_investigate": AUTO_INVESTIGATE, "agent_mode": runner._mode(None),
            "in_flight": sorted(State.in_flight)}


# ───────── built dashboard (P3) at /ui, so `make demo` is one command ─────────

_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if _DIST.exists():
    app.mount("/ui", StaticFiles(directory=_DIST, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/" if _DIST.exists() else "/docs")
