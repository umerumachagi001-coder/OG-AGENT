"""Supabase integration: sign-in for the dashboard and a copy of every case record in Supabase.

Two independent features, each switched on by its settings (`.env`):

    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_PUBLISHABLE_KEY=sb_publishable_...   → login required for the dashboard (safe to show in a browser)
    SUPABASE_SECRET_KEY=sb_secret_...             → case records are copied to Supabase (backend only, never sent out)

With neither set, everything works exactly as before: local, offline, no login.

Login: the dashboard signs in against Supabase Auth itself and sends the access token on every call. The backend
checks each token with Supabase (`GET /auth/v1/user`) and caches a valid answer briefly.

Records: the local SQLite database stays the working store (the mock company systems and "Reset demo" need it).
A background thread copies the case tables to the `oea_*` tables in Supabase (supabase/schema.sql) every few
seconds: new and changed rows only. Each demo session is a run (Reset demo starts a new one), so case ids that
restart after a reset never overwrite earlier runs. A Supabase outage never affects the demo: the copy catches up
when Supabase answers again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from agent import config as agent_config

log = logging.getLogger("supabase")


# ───────── settings ─────────

def settings() -> dict:
    """Read at call time, so .env changes and tests take effect without re-importing."""
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    publishable = (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    secret = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    return {
        "url": url,
        "publishable_key": publishable,
        "secret_key": secret,
        "auth_enabled": bool(url and publishable) and os.environ.get("OEA_REQUIRE_LOGIN", "1") != "0",
        "sync_enabled": bool(url and secret),
        "sync_seconds": float(os.environ.get("OEA_SUPABASE_SYNC_SECONDS", "3")),
    }


def public_config() -> dict:
    """What the dashboard needs to show a sign-in screen. The publishable key is designed to be public;
    the secret key is never included."""
    s = settings()
    return {"auth_enabled": s["auth_enabled"], "url": s["url"] if s["auth_enabled"] else None,
            "publishable_key": s["publishable_key"] if s["auth_enabled"] else None}


# ───────── sign-in: check a user's access token ─────────

class AuthError(Exception):
    pass


auth_client: Any = None   # an httpx-style client; tests put a simulated Supabase here
_token_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
TOKEN_CACHE_SECONDS = 60


def display_name(user: dict) -> str:
    meta = user.get("user_metadata") or {}
    return meta.get("full_name") or meta.get("name") or user.get("email") or user.get("id", "signed-in user")


def verify_token(token: str, http: Any | None = None) -> dict:
    """Return the Supabase user for an access token, or raise AuthError. Valid answers are cached for 60 s
    (keyed by a hash of the token, never the token itself)."""
    if not token:
        raise AuthError("Sign in required")
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _cache_lock:
        hit = _token_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    s = settings()
    client = http or auth_client or httpx
    try:
        r = client.get(f"{s['url']}/auth/v1/user", timeout=8,
                       headers={"apikey": s["publishable_key"] or s["secret_key"], "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise AuthError(f"Can't reach Supabase to check the sign-in: {e}") from e
    if r.status_code != 200:
        raise AuthError("Session expired or invalid; sign in again")
    user = r.json()
    user["display_name"] = display_name(user)
    with _cache_lock:
        if len(_token_cache) > 500:
            _token_cache.clear()
        _token_cache[key] = (now + TOKEN_CACHE_SECONDS, user)
    return user


# ───────── records: copy the case tables to Supabase ─────────

def _json(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"text": text}


# (local table, Supabase table, how to turn a SQLite row into a Supabase row)
SNAPSHOT_TABLES = [   # small, rows change: compare and push changed rows
    ("exceptions", "oea_exceptions", lambda r: {
        "id": r["id"], "order_id": r["order_id"], "domain": r["domain"], "severity": r["severity"],
        "status": r["status"], "symptom": r["symptom"], "root_cause": r["root_cause"], "confidence": r["confidence"],
        "created_at": r["created_at"], "resolved_at": r["resolved_at"], "origin": r["origin"]}),
    ("proposed_actions", "oea_proposed_actions", lambda r: {
        "id": r["id"], "exception_id": r["exception_id"], "rank": r["rank"], "action_type": r["action_type"],
        "params": _json(r["params_json"]), "cost": r["cost"], "risk": r["risk"], "rationale": r["rationale"],
        "requires_approval": bool(r["requires_approval"]), "status": r["status"]}),
    ("resolutions", "oea_resolutions", lambda r: {
        "exception_id": r["exception_id"], "action_id": r["action_id"], "outcome": r["outcome"],
        "human_feedback": r["human_feedback"], "resolved_by": r["resolved_by"],
        "minutes_to_resolve": r["minutes_to_resolve"]}),
]
APPEND_TABLES = [     # append-only: push rows with a higher id than last time
    ("investigation_trail", "oea_investigation_steps", lambda r: {
        "id": r["id"], "exception_id": r["exception_id"], "step_no": r["step_no"], "type": r["type"],
        "content": _json(r["content"]), "ts": r["ts"]}),
    ("action_log", "oea_action_log", lambda r: {
        "id": r["id"], "action_type": r["action_type"], "params": _json(r["params_json"]),
        "result": _json(r["result_json"]), "ok": bool(r["ok"]), "cost": r["cost"], "ts": r["ts"]}),
    ("notifications", "oea_notifications", lambda r: {
        "id": r["id"], "order_id": r["order_id"], "channel": r["channel"], "message": r["message"], "ts": r["ts"]}),
]
KEYS = {"oea_runs": "id", "oea_resolutions": "run_id,exception_id"}
BATCH = 500


class SupabaseSync:
    """Copies the local case tables to Supabase. `tick()` does one pass; `start()` runs it in a thread."""

    def __init__(self, http: Any | None = None, db_path: str | None = None, paused: Callable[[], bool] = lambda: False):
        self._http = http
        self.db_path = db_path
        self.paused = paused
        self.run_id: str | None = None
        self.hashes: dict[str, dict[Any, str]] = {}
        self.status = {"enabled": False, "run_id": None, "last_ok": None, "last_error": None, "rows_sent": 0}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # HTTP to PostgREST with the secret key (sent on the apikey header, as Supabase requires for these keys)
    @property
    def http(self):
        if self._http is None:
            s = settings()
            self._http = httpx.Client(base_url=f"{s['url']}/rest/v1", timeout=15,
                                      headers={"apikey": s["secret_key"], "Content-Type": "application/json"})
        return self._http

    def _upsert(self, table: str, rows: list[dict]):
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            r = self.http.post(f"/{table}", params={"on_conflict": KEYS.get(table, "run_id,id")}, json=chunk,
                               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            if r.status_code >= 300:
                raise RuntimeError(f"{table}: HTTP {r.status_code} {r.text[:300]}")
            self.status["rows_sent"] += len(chunk)

    def _delete(self, table: str, **eq):
        params = {k: f"eq.{v}" for k, v in eq.items()}
        r = self.http.delete(f"/{table}", params=params, headers={"Prefer": "return=minimal"})
        if r.status_code >= 300:
            raise RuntimeError(f"{table} delete: HTTP {r.status_code} {r.text[:300]}")

    # local bookkeeping lives in the SQLite file, so "Reset demo" (which rebuilds every table) starts a new run
    def _connect(self):
        conn = sqlite3.connect(self.db_path or agent_config.DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS supabase_sync (key TEXT PRIMARY KEY, value TEXT)")
        return conn

    def _get(self, conn, key, default=None):
        r = conn.execute("SELECT value FROM supabase_sync WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def _set(self, conn, key, value):
        conn.execute("INSERT OR REPLACE INTO supabase_sync(key, value) VALUES (?,?)", (key, str(value)))
        conn.commit()

    def tick(self) -> dict:
        """One copy pass. Returns what was sent. Raises on Supabase errors (the thread logs and retries)."""
        with self._lock:
            conn = self._connect()
            try:
                return self._tick(conn)
            finally:
                conn.close()

    def _tick(self, conn) -> dict:
        sent: dict[str, int] = {}
        run_id = self._get(conn, "run_id")
        if not run_id:
            run_id = str(uuid.uuid4())
            self._set(conn, "run_id", run_id)
        if run_id != self.run_id:                       # new run (first start or after Reset demo)
            self.hashes = {}
            self._upsert("oea_runs", [{"id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
                                       "engine": os.environ.get("OEA_ENGINE", "python"), "host": socket.gethostname()}])
            self.run_id = run_id
            self.status["run_id"] = run_id

        for local, remote, convert in SNAPSHOT_TABLES:  # exceptions first: the other tables reference them
            key_col = "exception_id" if local == "resolutions" else "id"
            seen = self.hashes.setdefault(remote, {})
            current, changed = {}, []
            for r in conn.execute(f"SELECT * FROM {local}"):
                row = {"run_id": run_id, **convert(r)}
                h = hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
                current[r[key_col]] = h
                if seen.get(r[key_col]) != h:
                    changed.append(row)
            if changed:
                self._upsert(remote, changed)
                sent[remote] = len(changed)
            for gone in set(seen) - set(current):       # e.g. a resolution withdrawn by Undo
                self._delete(remote, run_id=run_id, **{key_col: gone})
            self.hashes[remote] = current

        for local, remote, convert in APPEND_TABLES:
            mark = int(self._get(conn, f"mark:{local}", 0))
            rows = conn.execute(f"SELECT * FROM {local} WHERE id > ? ORDER BY id LIMIT ?", (mark, BATCH * 4)).fetchall()
            if rows:
                self._upsert(remote, [{"run_id": run_id, **convert(r)} for r in rows])
                self._set(conn, f"mark:{local}", rows[-1]["id"])
                sent[remote] = len(rows)
        self.status.update(last_ok=datetime.now(timezone.utc).isoformat(timespec="seconds"), last_error=None)
        return sent

    # background thread
    def start(self):
        self.status["enabled"] = True
        threading.Thread(target=self._run, daemon=True, name="supabase-sync").start()

    def stop(self):
        self._stop.set()

    def _run(self):
        delay = settings()["sync_seconds"]
        last_err = None
        while not self._stop.wait(delay):
            if self.paused():
                continue
            try:
                sent = self.tick()
                if sent:
                    log.info("copied to Supabase: %s", ", ".join(f"{k} {v}" for k, v in sent.items()))
                last_err, delay = None, settings()["sync_seconds"]
            except Exception as e:  # noqa: BLE001 — Supabase trouble must never stop the demo
                msg = str(e)[:300]
                self.status["last_error"] = msg
                if msg != last_err:
                    log.warning("Supabase copy failed (will retry): %s", msg)
                last_err, delay = msg, min(delay * 2, 60)


sync = SupabaseSync()
