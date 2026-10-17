"""Agent CLI (owner: P2). Services must be running (make services).

    python -m agent investigate 13                    # run on an existing exception
    python -m agent dev ORD-1001 inventory "Pick failed: item not found"   # create + investigate (until P4's detector exists)
    python -m agent approve 42 --by "Ops: Ruhaan"
    python -m agent reject 43 --by "Ops: Ruhaan" --feedback "too expensive"
    python -m agent trail 13                          # print the reasoning trail

Add --mode rules|llm to force a mode (default: OEA_AGENT_MODE / auto).
"""
from __future__ import annotations

import argparse
import json

from agent import runner, store


def _print(x):
    print(json.dumps(x, indent=2, default=str))


def _print_trail(exc_id: int):
    for s in store.get_trail(exc_id):
        c = s["content"]
        if s["type"] == "thought":
            line = f"💭 {c['text']}"
        elif s["type"] == "tool_call":
            line = f"→ {c['tool']}({json.dumps(c['input'])})"
        elif s["type"] == "tool_result":
            line = f"  {'✓' if c['ok'] else '✗'} {c.get('summary') or c.get('error') or ''}"
        else:
            rest = {k: v for k, v in c.items() if k not in ("kind", "evidence")}
            line = f"◆ {c['kind']}: {json.dumps(rest, default=str)[:220]}"
        print(f"{s['step_no']:>3} {line}")


def main():
    ap = argparse.ArgumentParser(prog="python -m agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("investigate"); p.add_argument("exception_id", type=int); p.add_argument("--mode")
    p = sub.add_parser("dev"); p.add_argument("order_id"); p.add_argument("domain"); p.add_argument("symptom")
    p.add_argument("--severity", default="medium"); p.add_argument("--mode")
    p = sub.add_parser("approve"); p.add_argument("action_id", type=int); p.add_argument("--by", default="human")
    p = sub.add_parser("reject"); p.add_argument("action_id", type=int); p.add_argument("--by", default="human")
    p.add_argument("--feedback")
    p = sub.add_parser("trail"); p.add_argument("exception_id", type=int)
    a = ap.parse_args()

    if a.cmd == "investigate":
        _print(runner.investigate(a.exception_id, mode=a.mode)); _print_trail(a.exception_id)
    elif a.cmd == "dev":
        eid = store.create_exception(a.order_id, a.domain, a.severity, a.symptom)
        print(f"Created exception #{eid}")
        _print(runner.investigate(eid, mode=a.mode)); _print_trail(eid)
    elif a.cmd == "approve":
        _print(runner.approve(a.action_id, by=a.by))
    elif a.cmd == "reject":
        _print(runner.reject(a.action_id, by=a.by, feedback=a.feedback))
    elif a.cmd == "trail":
        _print_trail(a.exception_id)


if __name__ == "__main__":
    main()
