# Demo Script: OrderGuard AI (5 minutes)

> **The line to land:** "When an order breaks, a person gets a decision, not an investigation."
> Every number below comes from the live system and was checked on the n8n stack (2026-10-16).

## Before the judges arrive (10 min)

| ✓ | Check |
|---|---|
| ☐ | `run-demo.bat` (or `python scripts/dev.py`) running; terminal says **ready** |
| ☐ | Tab 1: `http://localhost:8001` (OrderGuard). Tab 2: `http://localhost:5678` (n8n), signed in, **OEA 2 · Investigate and resolve** open |
| ☐ | Sidebar bottom says **n8n automation running** (green) |
| ☐ | Click **Reset demo** → Overview shows 0 high-risk, 0 awaiting approval |
| ☐ | "Approving as" box says your name (e.g. *Ruhaan, Ops lead*) |
| ☐ | Browser zoom 110–125% for the projector; notifications off; laptop on charge |
| ☐ | If `ANTHROPIC_API_KEY` is set, sidebar says **Diagnosis by Claude**; if not, **rule playbook** (both work) |

SC-1 and SC-5 both use ORD-1001. **Reset demo** between them.

---

## The run

### 1. Hook (30 s)
**Say:** "Order automation works until reality disagrees. A shelf is empty, a supplier slips, a card is declined. Today a person spends about 20 minutes per case, jumping between five systems to work out what happened. We built an automation that does the investigation, and only asks a person when the rules say it must."

### 2. Calm state (20 s)
**Show:** Overview. 50 orders on the **Order network** rings, by stage; nothing flagged.
**Say:** "Every dot is a real order in our mock company: placed, in the warehouse, on the road, delivered. n8n checks them every 10 seconds."

### 3. Two problems at once, fixed automatically: SC-5 (60 s)
**Click:** **Trigger a failure** → **Phantom stock with a tight deadline**. Click **ORD-1001** on the ring (or in Priority queue).
**Point at, top to bottom:**
- *What was detected:* "Pick failed at WH-A … delivery promise also at risk." One case, **High**.
- *Assessment:* phantom stock (system says 5, shelf says 0) **and** only 72 h left, so standard shipping is late.
- Key facts cards, each citing the check it came from.
- Green box: **Fixed automatically**: ship from Mumbai Hub by Express Air, **₹470**.

**Say:** "It checked stock, found the count was wrong, found real stock in Mumbai, then noticed that even with stock, standard shipping misses the promise. So it did both: moved the stock and upgraded shipping. ₹470 is inside the policy, so nobody had to be asked."
**Optional (10 s):** scroll the **Investigation timeline**. Each line is labelled *Observed*, *Reasoning*, *Assessment*, *Policy* or *Action ran*.

### 4. A fix that needs a person: SC-2 (60 s)
**Click:** **Trigger a failure** → **Supplier misses ship date**. Amber banner appears → **Open approvals**.
**Point at:** the problem (desk supplier slipped 6 days, delivery 5.5 days late), then the options with cost and the reasons a person is needed: *total cost ₹2,350 > ₹1,000* and *switch supplier always needs approval*.
**Click:** **Approve** on *Switch to OfficeWorks Supply + EXPRESS* (₹2,350) → confirm. The second option, *keep supplier and tell the customer*, needs a person because its risk is high.
**Say:** "Switching supplier alone is still late. The agent worked out it needs a new supplier **and** express shipping. The policy says supplier switches always need a human. I approve, and the paused n8n run picks up and executes it." The case flips to **Fixed after approval**.

### 5. Show it's really n8n (30 s)
**Switch to Tab 2** (n8n). Show the **OEA 2** canvas: claim → evidence by problem type → Claude (with a rule-playbook fallback) → policy → hold → auto-fix *or* Slack ping + **Wait** → approved? → run.
**Click:** **Executions** → the latest runs are green. The SC-2 run shows it waited, then resumed.
**Say:** "The whole process is an n8n workflow the ops team can read and change. The approval policy is a JSON file."

### 6. Guardrails: fraud is never automatic (30 s)
**Back to Tab 1. Click:** **Trigger a failure** → **Payment declined — fraud signals**.
**Point at:** **High** severity; order **put on hold straight away**; no retry; waiting for a person.
**Say:** "Three declines in four minutes, fraud score 0.91, IP in another country. It doesn't retry the card. It freezes the order and asks a human. Compare an expired card: there it just emails the customer."

### 7. Impact (30 s)
**Click:** **Overview** → **Impact** panel.
**Say the live numbers**, e.g.: "57% of cases fixed with no person; 30 seconds on average versus 15 minutes when a person is involved; 2.6 hours of analyst time saved on 14 cases, at an assumed 20 minutes per case by hand; ₹1.78 lakh of orders protected for about ₹6,000 in fix costs." (Includes the 12 seeded past cases. The panel says so.)

### 8. Close (30 s)
**Click:** **Audit trail**.
**Say:** "Every check, every policy decision, every approval: who, what, when, why. The AI only reads; it can't change anything. Code re-checks its costs. Rules decide what needs a human. And any automatic fix can be undone in one click. Next: real ERP and payment connectors, and learning from approved fixes."

**Timing check:** 30 + 20 + 60 + 60 + 30 + 30 + 30 + 30 = **4 min 50 s**.

---

## Judge Q&A: show, don't tell

| Question | Answer + what to click |
|---|---|
| **"What if the AI gets it wrong?"** | Three layers: it proposes only (read-only tools); code re-verifies every cost from live data; the policy decides. And **Undo**: open the SC-5 case → **Undo this fix** → confirm. Both steps reverse, the ₹470 is reversed, and the case comes back to you. |
| "What exactly does the AI do?" | Reads the evidence n8n gathered, names the root cause with a confidence score, proposes 1–3 ranked fixes with cost, risk and impact, and drafts the customer message. It doesn't decide or act. |
| "What if Claude is down?" | n8n falls back to rule playbooks with the same output; the timeline says "Switched to the rule playbook". |
| "Can it overspend?" | Policy limits: e.g. stock moves ≤ ₹200 extra, shipping upgrades ≤ ₹500, any option ≤ ₹1,000; supplier switches, price changes and refunds always need a person; VIP or > ₹50,000 orders always escalate. Show `agent/policy.json`. |
| "Is the data real?" | Mock company systems (orders, stock, suppliers, payments, pricing, delivery) with real API contracts. Swapping in real systems means replacing those endpoints, not the workflow. |
| "Why n8n?" | The process is visible and editable by ops, not hidden in code; approvals pause and resume natively; Slack ping built in. |
| "Can a judge try it?" | Yes: hand them the mouse. **Trigger a failure** → any of the six; **Reset demo** first if they pick SC-1 or SC-5. |

---

## If something goes wrong

| Problem | Do this |
|---|---|
| Red banner "The backend isn't answering" | Check the terminal; restart `run-demo.bat`. Takes ~30 s. |
| Sidebar says n8n is down | Keep going in Python mode: stop, run `python scripts/dev.py --python`. Same screens, no n8n tab. |
| A case sits on "Investigating" > 10 s | **Reset demo** and trigger it again. (If it shows "The automation stopped", click **Investigate again**.) |
| Approve shows "no result yet" | Open the n8n **Executions** tab; the run is there. Reset demo before the next scenario. |
| Laptop or projector fails | Play the backup video (`demo/backup.mp4`). Record it after the final rehearsal. |

## Reference: all scenarios

| Trigger menu item | Order | What happens | Cost |
|---|---|---|---|
| Phantom stock | ORD-1001 | Fixed automatically: ship from Mumbai Hub | ₹120 |
| Phantom stock with a tight deadline | ORD-1001 | Fixed automatically: Mumbai Hub **+** Express Air (one high-severity case) | ₹470 |
| Supplier misses ship date | ORD-1002 | Needs a person: switch to OfficeWorks Supply + Express | ₹2,350 |
| Payment declined — expired card | ORD-1003 | Fixed automatically: customer emailed to update card, no retry | ₹0 |
| Payment declined — fraud signals | ORD-1003 | Held at once, high severity, needs a person | — |
| Discount stacking below margin | ORD-1004 | Held, needs a person: correct price to ₹15,199.20 (or reject) | — |
