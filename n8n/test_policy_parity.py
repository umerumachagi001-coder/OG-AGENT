"""The n8n policy node (n8n/policy_engine.js) must decide exactly like agent/policy.py.

Runs both on the same random options/contexts and compares auto, reasons and checks. Needs Node.js.
    python -m pytest n8n/test_policy_parity.py -q
"""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from agent import policy

JS = Path(__file__).with_name("policy_engine.js")
STEP_TYPES = ["reallocate_stock", "switch_supplier", "retry_payment", "hold_order", "release_order",
              "apply_price_correction", "expedite_shipping", "notify_customer"]


def cases(n=400, seed=7):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        steps = [{"type": rng.choice(STEP_TYPES), "est_cost": rng.choice([0, 40, 120, 199, 200, 201, 350, 500, 900, 2000])}
                 for _ in range(rng.choice([0, 1, 1, 2, 3]))]
        option = {"steps": steps, "risk": rng.choice(["low", "medium", "high"])}
        ctx = {"customer_tier": rng.choice(["VIP", "Gold", "Standard"]),
               "order_value": rng.choice([899, 15998, 49999, 50000, 50001, 120000]),
               "confidence": rng.choice([None, 0.3, 0.69, 0.7, 0.92]),
               "fraud_score": rng.choice([None, 0.02, 0.29, 0.3, 0.5, 0.51, 0.91]),
               "payment_attempts": rng.choice([0, 1, 2, 3])}
        out.append((option, ctx))
    return out


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_js_policy_matches_python():
    pol = policy.load()
    cs = cases()
    runner = (
        "const fs=require('fs');const src=fs.readFileSync(process.argv[1],'utf8');"
        "const m={exports:{}};new Function('module',src)(m);"
        "const {cases,pol}=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify(cases.map(([o,c])=>m.exports.evaluate(o,c,pol))));"
    )
    r = subprocess.run(["node", "-e", runner, str(JS)], input=json.dumps({"cases": cs, "pol": pol}),
                       capture_output=True, text=True, encoding="utf-8", check=True)
    js = json.loads(r.stdout)
    for (option, ctx), j in zip(cs, js):
        py = policy.evaluate(option, ctx, pol).as_dict()
        assert j == py, (option, ctx, j, py)
