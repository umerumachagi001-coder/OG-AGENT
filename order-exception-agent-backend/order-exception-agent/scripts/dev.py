"""Start the whole backend with one command (NFR-4). Works on macOS, Linux and Windows.

    python scripts/dev.py            # or: make demo
      dashboard     http://localhost:8001        (built UI, served by the orchestrator)
      services      http://localhost:8000/docs
      orchestrator  http://localhost:8001/docs

Ctrl+C stops both. Reads .env if present.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip())


def _sigterm(*_):
    raise KeyboardInterrupt  # so `kill` also runs the cleanup below and doesn't orphan the servers


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    load_env()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "services.app:app", "--port", "8000", "--log-level", "warning"],
                         cwd=ROOT, env=env),
    ]
    time.sleep(1.5)  # services must be up (and seeded) before the detector loop starts
    procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "orchestrator.app:app", "--port", "8001"],
                                  cwd=ROOT, env=env))
    mode = os.environ.get("OEA_AGENT_MODE", "auto")
    key = "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set → rules mode"
    ui = ("http://localhost:8001" if (ROOT / "dashboard" / "dist").exists()
          else "not built yet. Run: cd dashboard && npm install && npm run build")
    print(f"\n  dashboard     {ui}\n  services      http://localhost:8000/docs\n  orchestrator  http://localhost:8001/docs\n"
          f"  agent mode    {mode} (ANTHROPIC_API_KEY {key})\n  Ctrl+C to stop\n", flush=True)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT) if os.name != "nt" else p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
