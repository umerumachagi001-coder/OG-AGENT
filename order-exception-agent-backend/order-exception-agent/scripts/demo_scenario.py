"""Run one scenario end to end against a running stack and print what the judges would see.

    python scripts/demo_scenario.py sc1              # or: make demo-sc1
    python scripts/demo_scenario.py sc2 --approve    # also approve the top option
    python scripts/demo_scenario.py sc4 --no-reset

Needs `make demo` running in another terminal.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

API = "http://127.0.0.1:8001"  # not localhost: avoids a 2 s IPv6 stall per connection on Windows


def line(step):
    c = step["content"]
    t = step["type"]
    if t == "thought":
        return f"💭 {c['text']}"
    if t == "tool_call":
        return f"→  {c['tool']}({', '.join(f'{k}={v}' for k, v in c['input'].items())})"
    if t == "tool_result":
        return f"   {'✓' if c['ok'] else '✗'} {c.get('summary') or c.get('error') or ''}"
    k = c.get("kind")
    if k == "diagnosis":
        return f"◆  ROOT CAUSE ({c['confidence']:.0%}): {c['root_cause']}"
    if k == "policy":
        return "◆  POLICY: " + " | ".join(f"#{o['rank']} {o['title']}: {'AUTO' if o['auto'] else 'needs human — ' + '; '.join(o['reasons'])}"
                                          for o in c["options"])
    if k == "execution":
        return f"◆  EXECUTED: {'; '.join(c['steps'])} (₹{c['actual_cost']:,.0f})"
    if k == "escalation":
        return f"◆  ESCALATED: {c['reason']}"
    return f"◆  {k.upper()}: {json.dumps({x: y for x, y in c.items() if x != 'kind'}, default=str)[:160]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--no-reset", action="store_true")
    a = ap.parse_args()
    http = httpx.Client(base_url=API, timeout=60)
    try:
        http.get("/health")
    except httpx.HTTPError:
        sys.exit("Stack not running — start it with `make demo` first.")

    if not a.no_reset:
        http.post("/reset")
    t0 = time.time()
    r = http.post("/trigger", params={"fail": a.scenario}).json()
    print(f"\n💥 {r['id']} {r['title']}: {r['summary']}\n")
    if not r["exceptions_created"]:
        sys.exit("Detector created no exception (already open?)")
    eid = r["exceptions_created"][0]

    seen = 0
    while True:
        for s in http.get(f"/exceptions/{eid}/trail", params={"after": seen}).json():
            print(line(s), flush=True)
            seen = s["step_no"]
        e = http.get(f"/exceptions/{eid}").json()
        if e["status"] in ("auto_resolved", "awaiting_approval", "failed") and not e["investigating"]:
            break
        time.sleep(0.3)
    print(f"\n⏱  {e['status'].upper()} in {time.time() - t0:.1f}s  (severity {e['severity']})")

    if e["status"] == "awaiting_approval" and a.approve:
        top = e["actions"][0]
        print(f"\n👤 Approving #{top['rank']} {top['params']['title']} …")
        out = http.post(f"/actions/{top['id']}/approve", json={"by": "Ops: Demo"}).json()
        for s in http.get(f"/exceptions/{eid}/trail", params={"after": seen}).json():
            print(line(s))
        print(f"⏱  {out['status'].upper()}")
    m = http.get("/metrics").json()
    print(f"\n📊 auto-resolved {m['auto_resolved_pct']}% · analyst minutes saved {m['analyst_minutes_saved']} · "
          f"revenue protected ₹{m['revenue_protected']:,.0f}\n")


if __name__ == "__main__":
    main()
