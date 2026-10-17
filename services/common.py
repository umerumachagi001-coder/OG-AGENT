"""Shared helpers for the mock services (owner: P1).

- One SQLite file (path from $OEA_DB, default data/oea.db).
- `fail_flag` dependency: every read endpoint accepts ?fail=timeout|error|slow so P2 can
  test the agent's error handling and NFR-5 fallback without touching data.
- Scenario failures (?fail=sc1 … sc4) live in services/scenarios.py and change the DB,
  so the detector, the agent and the dashboard all see the same broken state.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Query

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("OEA_DB", ROOT / "data" / "oea.db"))

ORDER_STATUSES = [
    "placed", "payment_pending", "paid", "allocated", "picking", "pick_failed",
    "awaiting_stock", "pricing_review", "payment_failed", "on_hold",
    "packed", "shipped", "delivered", "cancelled",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def now_iso() -> str:
    return iso(utcnow())


def days_from_now(days: float) -> str:
    return iso(utcnow() + timedelta(days=days))


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # dashboard + agent read while services write
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


def add_history(conn, order_id: str, status: str, note: str | None = None, ts: str | None = None):
    conn.execute(
        "INSERT INTO order_status_history(order_id, status, note, ts) VALUES (?,?,?,?)",
        (order_id, status, note, ts or now_iso()),
    )


def set_order_status(conn, order_id: str, status: str, note: str | None = None):
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    add_history(conn, order_id, status, note)


def log_action(conn, action_type: str, params: dict, result: dict, ok: bool, cost: float = 0.0) -> int:
    """Append to the system action log; returns the log id (used to undo the action later, FR-15)."""
    return conn.execute(
        "INSERT INTO action_log(action_type, params_json, result_json, ok, cost, ts) VALUES (?,?,?,?,?,?)",
        (action_type, json.dumps(params), json.dumps(result), int(ok), cost, now_iso()),
    ).lastrowid


def fail_flag(fail: str | None = Query(None, description="Inject a transport failure: timeout | error | slow")):
    """FastAPI dependency. Scenario names (sc1…) are ignored here — they are handled by /admin/trigger."""
    if fail == "timeout":
        time.sleep(2)
        raise HTTPException(504, detail={"error": "upstream_timeout", "message": "Mock system timed out"})
    if fail == "error":
        raise HTTPException(503, detail={"error": "service_unavailable", "message": "Mock system unavailable"})
    if fail == "slow":
        time.sleep(3)
    return fail
