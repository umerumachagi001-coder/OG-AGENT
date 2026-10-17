"""Start everything with one command (NFR-4). Works on macOS, Linux and Windows.

    python scripts/dev.py              n8n runs the process (default; needs Node.js 20–24)
    python scripts/dev.py --python     built-in Python engine instead of n8n (no Node needed)

      dashboard     http://localhost:8001
      n8n editor    http://localhost:5678   (workflows "OEA 1 · Detect exceptions", "OEA 2 · Investigate and resolve")
      services      http://localhost:8000/docs

n8n: uses an n8n already running on OEA_N8N_URL if there is one; otherwise starts n8n 1.x with npx,
keeping its data in .n8n-data/ and (re)importing n8n/workflows/*.json whenever they change.
Ctrl+C stops what this script started. Reads .env if present.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
N8N_VERSION = "1.123.85"            # last 1.x line; runs on Node 20–24
N8N_DATA = ROOT / ".n8n-data"
WORKFLOW_IDS = ["oeaDetect0000001", "oeaInvestigate01"]


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


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _workflows_live(url: str) -> bool:
    """n8n answers /healthz before it has activated the workflows; wait until the scan webhook works.
    Calling it is harmless: on a healthy system the detector finds nothing."""
    try:
        req = urllib.request.Request(f"{url}/webhook/oea-scan", data=b"{}", method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _n8n_env() -> dict:
    env = {**os.environ,
           "N8N_USER_FOLDER": str(N8N_DATA), "N8N_PORT": "5678", "N8N_LISTEN_ADDRESS": "127.0.0.1",
           "WEBHOOK_URL": "http://127.0.0.1:5678/", "N8N_BLOCK_ENV_ACCESS_IN_NODE": "false",
           "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_PERSONALIZATION_ENABLED": "false",
           "N8N_SECURE_COOKIE": "false", "N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS": "false",
           "N8N_RUNNERS_ENABLED": "true", "DB_SQLITE_POOL_SIZE": "2"}
    env.setdefault("OEA_API_URL", "http://127.0.0.1:8001")
    env.setdefault("GENERIC_TIMEZONE", "Asia/Kolkata")
    return env


def _npx() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def start_n8n(procs: list) -> bool:
    url = os.environ.get("OEA_N8N_URL", "http://127.0.0.1:5678").rstrip("/")
    if _up(f"{url}/healthz"):
        if _workflows_live(url):
            print(f"  n8n           using the n8n already running at {url}")
            return True
        print(f"  n8n           running at {url}, but the OEA workflows aren't active there. Import "
              f"n8n/workflows/*.json and switch both on, then restart this script.")
        return False
    installed = shutil.which("n8n") or shutil.which("n8n.cmd")
    npx = _npx()
    if not (installed or npx):
        print("  n8n           Node.js not found. Install Node 20–24, or run with --python.")
        return False
    env = _n8n_env()
    n8n = [installed] if installed else [npx, "--yes", f"n8n@{N8N_VERSION}"]
    wf_dir = ROOT / "n8n" / "workflows"
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(wf_dir.glob("*.json")))).hexdigest()[:16]
    marker = N8N_DATA / f".oea-workflows-{digest}"
    if not marker.exists():
        print("  n8n           importing workflows (first run downloads n8n, this can take a few minutes)…", flush=True)
        N8N_DATA.mkdir(exist_ok=True)
        for f in sorted(wf_dir.glob("*.json")):
            subprocess.run(n8n + ["import:workflow", f"--input={f}"], env=env, cwd=ROOT, check=True)
        for wid in WORKFLOW_IDS:
            subprocess.run(n8n + ["update:workflow", f"--id={wid}", "--active=true"], env=env, cwd=ROOT, check=True)
        for old in N8N_DATA.glob(".oea-workflows-*"):
            old.unlink()
        marker.touch()
    procs.append(subprocess.Popen(n8n + ["start"], env=env, cwd=ROOT))
    for _ in range(120):
        if _workflows_live(url):
            return True
        time.sleep(1)
    print("  n8n           did not come up within 90 s; check the n8n output above.")
    return False


def _port_busy(port: int) -> bool:
    """True if something already listens on 127.0.0.1:port (e.g. an earlier copy of this project)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


STOP_HINT = {
    "nt": ("  Stop it in PowerShell, then run this again:\n"
           "    Get-NetTCPConnection -LocalPort 8000,8001,5678 -State Listen | "
           "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"),
    "posix": ("  Stop it, then run this again:\n"
              "    kill $(lsof -t -iTCP:8000 -iTCP:8001 -iTCP:5678 -sTCP:LISTEN)"),
}


def _already_running(busy: list[int]) -> None:
    print(f"\n  Port {' and '.join(map(str, busy))} already in use: OrderGuard is probably already running"
          " (another terminal or run-demo.bat window).\n"
          "  Use that copy: http://localhost:8001\n"
          f"{STOP_HINT.get(os.name, STOP_HINT['posix'])}\n", flush=True)


def _alive(proc, name: str) -> bool:
    if proc.poll() is None:
        return True
    print(f"\n  {name} stopped right after starting (exit code {proc.returncode}); see the error above.\n", flush=True)
    return False


READY = ROOT / ".oea-ready"   # written once everything (incl. n8n workflows) is live; read by wait_ready.py


def main():
    signal.signal(signal.SIGTERM, _sigterm)
    READY.unlink(missing_ok=True)
    load_env()
    if "--python" in sys.argv:
        os.environ["OEA_ENGINE"] = "python"
    os.environ.setdefault("OEA_ENGINE", "n8n")
    engine = os.environ["OEA_ENGINE"]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    busy = [p for p in (8000, 8001) if _port_busy(p)]
    if busy:
        _already_running(busy)
        sys.exit(1)
    procs: list = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "services.app:app", "--port", "8000",
                                       "--log-level", "warning"], cwd=ROOT, env=env))
        time.sleep(1.5)  # services must be up (and seeded) before anything scans
        if not _alive(procs[0], "The mock systems (port 8000)"):
            return
        orch = [sys.executable, "-m", "uvicorn", "orchestrator.app:app", "--port", "8001", "--log-level", "warning"]
        procs.append(subprocess.Popen(orch, cwd=ROOT, env=env))   # n8n's workflows call this, so it starts first
        time.sleep(1.5)
        if not _alive(procs[1], "The orchestrator (port 8001)"):
            return
        if engine == "n8n" and not start_n8n(procs):
            print("  falling back to the built-in Python engine")
            engine = env["OEA_ENGINE"] = "python"
            procs[1].terminate()
            procs[1].wait(timeout=8)
            procs[1] = subprocess.Popen(orch, cwd=ROOT, env=env)
        key = "set" if os.environ.get("ANTHROPIC_API_KEY") else "not set, so rule playbooks diagnose"
        ui = ("http://localhost:8001" if (ROOT / "dashboard" / "dist").exists()
              else "not built yet. Run: cd dashboard && npm install && npm run build")
        print(f"\n  dashboard     {ui}\n"
              + (f"  n8n editor    http://localhost:5678\n" if engine == "n8n" else "")
              + f"  services      http://localhost:8000/docs\n"
              f"  engine        {engine}   ·   Claude: ANTHROPIC_API_KEY {key}\n  Ctrl+C to stop\n", flush=True)
        READY.write_text(engine)
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("\n  A part of the project stopped unexpectedly (see the error above); stopping the rest.\n", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        READY.unlink(missing_ok=True)
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT) if os.name != "nt" else p.terminate()
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
