"""Agent settings (owner: P2). All overridable with environment variables / .env."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first and each new connection
# stalls ~2 s before falling back, because uvicorn only listens on IPv4.
SERVICES_URL = os.environ.get("OEA_SERVICES_URL", "http://127.0.0.1:8000")
DB_PATH = Path(os.environ.get("OEA_DB", ROOT / "data" / "oea.db"))
POLICY_PATH = Path(os.environ.get("OEA_POLICY", Path(__file__).with_name("policy.json")))

# llm   → always use Claude (fails over to rules on error)
# rules → deterministic playbooks only (no API key needed; demo safety net)
# auto  → llm if ANTHROPIC_API_KEY is set, else rules
AGENT_MODE = os.environ.get("OEA_AGENT_MODE", "auto")
MODEL = os.environ.get("OEA_MODEL", "claude-sonnet-5-5")
MAX_TOOL_CALLS = int(os.environ.get("OEA_MAX_TOOL_CALLS", "8"))
LLM_TIMEOUT_S = float(os.environ.get("OEA_LLM_TIMEOUT", "25"))   # NFR-1: whole investigation < 30 s
HTTP_TIMEOUT_S = float(os.environ.get("OEA_HTTP_TIMEOUT", "5"))
