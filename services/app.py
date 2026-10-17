"""Mock order systems — one FastAPI app (owner: P1). See ADR-005.

    uvicorn services.app:app --port 8000 --reload      # or: make services
    open http://localhost:8000/docs                      # interactive API docs

Admin (demo control, used by the dashboard's "Trigger failure" panel — FR-18):
    GET  /admin/scenarios             list scenarios
    POST /admin/trigger?fail=sc1      break a demo order (sc1 | sc2 | sc3_expired | sc3_fraud | sc4)
    POST /admin/reset                 restore the clean seeded world (wipes exceptions too)
    GET  /admin/action_log            every action executed (audit, NFR-3)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from data import seed
from services import scenarios
from services.api_actions import router as actions_router
from services.api_read import router as read_router
from services.common import DB_PATH, db, now_iso, rows



@asynccontextmanager
async def lifespan(_app):
    if not DB_PATH.exists():
        seed.build()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Order Exception Agent — mock systems",
    description="Orders, inventory, supplier, payment, pricing and SLA mocks with injectable failures.",
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(read_router)
app.include_router(actions_router)


@app.get("/health", tags=["admin"])
def health():
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    return {"ok": True, "orders": n, "db": str(DB_PATH), "time": now_iso()}


@app.get("/admin/scenarios", tags=["admin"])
def list_scenarios():
    return scenarios.describe()


@app.post("/admin/trigger", tags=["admin"])
def trigger(fail: str = Query(..., description="sc1 | sc2 | sc3_expired | sc3_fraud | sc4")):
    if not scenarios.resolve(fail):
        raise HTTPException(422, detail={"error": "unknown_scenario", "message": f"Unknown scenario '{fail}'",
                                         "available": list(scenarios.SCENARIOS)})
    with db() as c:
        return scenarios.trigger(c, fail)


@app.post("/admin/reset", tags=["admin"])
def reset():
    counts = seed.build()
    return {"ok": True, "reset_at": now_iso(), "counts": counts}


@app.get("/admin/action_log", tags=["admin"])
def action_log(limit: int = 50):
    with db() as c:
        return rows(c.execute("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)))
