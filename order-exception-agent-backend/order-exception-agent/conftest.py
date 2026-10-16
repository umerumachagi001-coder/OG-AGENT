"""Shared test setup: one temporary database and rules mode for every test suite.

Settings are read once at import, so they must be set before any project module loads.
This lets `pytest services/tests agent/tests orchestrator/tests` run in one process.
"""
import os
import tempfile

os.environ.setdefault("OEA_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("OEA_DISABLE_LOOP", "1")
os.environ.setdefault("OEA_AGENT_MODE", "rules")
