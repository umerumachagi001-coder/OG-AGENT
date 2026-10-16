"""DB access for the agent: exceptions, investigation trail, proposed actions, resolutions (owner: P2).

Trail `content` is always a JSON object so the dashboard can render it (FR-7):
    thought      {"text": str, "source": "llm"|"rules"}
    tool_call    {"tool": str, "input": {...}}
    tool_result  {"tool": str, "ok": bool, "summary": str, "data": {...} | null, "error": str | null}
    decision     {"kind": "diagnosis"|"policy"|"containment"|"execution"|"escalation"|"fallback"|"human", ...}
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from agent import config


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def _ro():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _one(sql, args=()):
    with _ro() as c:
        r = c.execute(sql, args).fetchone()
        return dict(r) if r else None


def _exec(sql, args=()):
    conn = connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ───────── exceptions ─────────

def get_exception(exc_id: int) -> dict | None:
    return _one("SELECT * FROM exceptions WHERE id=?", (exc_id,))


def create_exception(order_id: str, domain: str, severity: str, symptom: str) -> int:
    """Normally the detector (P4) does this; exposed for tests and dev CLI."""
    return _exec("INSERT INTO exceptions(order_id, domain, severity, status, symptom, created_at) VALUES (?,?,?,?,?,?)",
                 (order_id, domain, severity, "open", symptom, now_iso()))


def set_status(exc_id: int, status: str, resolved: bool = False):
    if resolved:
        _exec("UPDATE exceptions SET status=?, resolved_at=? WHERE id=?", (status, now_iso(), exc_id))
    else:
        _exec("UPDATE exceptions SET status=? WHERE id=?", (status, exc_id))


def save_diagnosis(exc_id: int, root_cause: str, confidence: float):
    _exec("UPDATE exceptions SET root_cause=?, confidence=? WHERE id=?", (root_cause, confidence, exc_id))


# ───────── trail ─────────

class Trail:
    def __init__(self, exc_id: int):
        self.exc_id = exc_id
        r = _one("SELECT COALESCE(MAX(step_no),0) AS n FROM investigation_trail WHERE exception_id=?", (exc_id,))
        self.n = r["n"]

    def add(self, type_: str, content: dict):
        self.n += 1
        _exec("INSERT INTO investigation_trail(exception_id, step_no, type, content, ts) VALUES (?,?,?,?,?)",
              (self.exc_id, self.n, type_, json.dumps(content, default=str), now_iso()))

    def thought(self, text: str, source: str = "llm"):
        if text and text.strip():
            self.add("thought", {"text": text.strip(), "source": source})

    def tool_call(self, tool: str, args: dict):
        self.add("tool_call", {"tool": tool, "input": args})

    def tool_result(self, tool: str, data=None, error: str | None = None, summary: str = ""):
        self.add("tool_result", {"tool": tool, "ok": error is None, "summary": summary, "data": data, "error": error})

    def decision(self, kind: str, **content):
        self.add("decision", {"kind": kind, **content})


def get_trail(exc_id: int) -> list[dict]:
    with _ro() as c:
        out = [dict(r) for r in c.execute(
            "SELECT step_no, type, content, ts FROM investigation_trail WHERE exception_id=? ORDER BY step_no", (exc_id,))]
    for r in out:
        r["content"] = json.loads(r["content"])
    return out


# ───────── proposed actions ─────────

def save_option(exc_id: int, rank: int, option: dict, verdict: dict) -> int:
    steps = option.get("steps") or []
    action_type = "+".join(s["type"] for s in steps) or "none"
    params = {"title": option.get("title"), "steps": steps, "impact": option.get("impact"),
              "policy": verdict}
    return _exec(
        "INSERT INTO proposed_actions(exception_id, rank, action_type, params_json, cost, risk, rationale,"
        " requires_approval, status) VALUES (?,?,?,?,?,?,?,?,?)",
        (exc_id, rank, action_type, json.dumps(params), float(option.get("cost") or 0),
         option.get("risk", "medium"), option.get("rationale"), int(not verdict["auto"]), "proposed"))


def get_action(action_id: int) -> dict | None:
    a = _one("SELECT * FROM proposed_actions WHERE id=?", (action_id,))
    if a:
        a["params"] = json.loads(a["params_json"])
    return a


def get_actions(exc_id: int) -> list[dict]:
    with _ro() as c:
        out = [dict(r) for r in c.execute("SELECT * FROM proposed_actions WHERE exception_id=? ORDER BY rank", (exc_id,))]
    for a in out:
        a["params"] = json.loads(a["params_json"])
    return out


def set_action_status(action_id: int, status: str):
    _exec("UPDATE proposed_actions SET status=? WHERE id=?", (status, action_id))


def write_resolution(exc_id: int, action_id: int | None, outcome: str, resolved_by: str, feedback: str | None = None):
    e = get_exception(exc_id)
    mins = None
    if e and e.get("created_at"):
        mins = round((datetime.now(timezone.utc) - datetime.fromisoformat(e["created_at"])).total_seconds() / 60, 2)
    _exec("INSERT OR REPLACE INTO resolutions VALUES (?,?,?,?,?,?)",
          (exc_id, action_id, outcome, feedback, resolved_by, mins))
