"""Exit 0 once scripts/dev.py reports everything is live (n8n workflows included).

Used by run-demo.bat to open the browser at the right time, and by n8n/e2e_check.py.
"""
import sys
import time
from pathlib import Path

READY = Path(__file__).resolve().parent.parent / ".oea-ready"

for _ in range(900):  # up to 15 minutes: the first run downloads n8n
    if READY.exists():
        sys.exit(0)
    time.sleep(1)
sys.exit(1)
