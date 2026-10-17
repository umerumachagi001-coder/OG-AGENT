"""Supabase endpoints (sign-in + records), kept in their own file.

    GET  /auth/config        public: is login on, and the Supabase URL + publishable key the sign-in screen needs
    GET  /auth/me            the signed-in user (needs a valid token)
    GET  /supabase/status    is the copy to Supabase on, which run, last success, last error
    POST /supabase/sync      copy now instead of waiting for the next pass

Plus `require_login`, the middleware that protects the dashboard API when login is on.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from orchestrator import supabase_integration as sb

router = APIRouter(tags=["supabase"])

# Reachable without signing in. Everything else on the orchestrator needs a valid Supabase token when login is on.
PUBLIC_PREFIXES = (
    "/auth/config",   # the sign-in screen needs it before anyone is signed in
    "/health",        # liveness only: counts, no case data
    "/ui",            # the dashboard's own files (it shows the sign-in screen)
    "/n8n/",          # n8n's calls into the backend; the orchestrator only listens on this machine (127.0.0.1)
    "/docs", "/openapi.json", "/redoc",
)


def _public(path: str) -> bool:
    return path == "/" or path.startswith(PUBLIC_PREFIXES)


async def require_login(request: Request, call_next):
    """When login is on: reject requests without a valid token, and put the user on request.state.user."""
    request.state.user = None
    if request.method == "OPTIONS" or not sb.settings()["auth_enabled"] or _public(request.url.path):
        return await call_next(request)
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    try:
        request.state.user = sb.verify_token(token)
    except sb.AuthError as e:
        return JSONResponse({"detail": str(e)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


def acting_user(request: Request, fallback: str) -> str:
    """Who a decision is recorded against: the signed-in person when login is on, else the typed name."""
    user = getattr(request.state, "user", None)
    return user["display_name"] if user else fallback


@router.get("/auth/config")
def auth_config():
    return sb.public_config()


@router.get("/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if not sb.settings()["auth_enabled"]:
        return {"auth_enabled": False, "user": None}
    if not user:
        raise HTTPException(401, "Sign in required")
    return {"auth_enabled": True, "user": {"id": user.get("id"), "email": user.get("email"),
                                           "name": user["display_name"]}}


@router.get("/supabase/status")
def status():
    s = sb.settings()
    return {**sb.sync.status, "enabled": s["sync_enabled"], "auth_enabled": s["auth_enabled"],
            "project": s["url"] or None}


@router.post("/supabase/sync")
def sync_now():
    if not sb.settings()["sync_enabled"]:
        raise HTTPException(409, "Supabase copy is off: set SUPABASE_URL and SUPABASE_SECRET_KEY in .env")
    try:
        return {"sent": sb.sync.tick(), **sb.sync.status}
    except Exception as e:  # noqa: BLE001
        sb.sync.status["last_error"] = str(e)[:300]
        raise HTTPException(502, f"Supabase copy failed: {e}") from e
