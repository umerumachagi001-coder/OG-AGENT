# n8n automation

The exception process runs as two n8n workflows. Python provides the company systems they call
(mock order, stock, supplier, payment, pricing, delivery APIs) and a bridge that records every step
so the dashboard can show it.

![Investigate and resolve](workflow-2-investigate.png)

## The workflows

### OEA 1 · Detect exceptions (`workflows/1-detect-exceptions.json`)
| Step | What it does |
|---|---|
| Every 10 seconds / Scan now (webhook `oea-scan`) | Two ways to start: a schedule, and the dashboard's **Trigger a failure** |
| Run detector rules | `POST /n8n/scan`: 6 rules over all open orders, opens one exception per new problem |
| Reply to caller | Returns the new exception ids to the dashboard |
| Anything new? → One item per exception → Start investigation | Starts workflow 2 once per exception, without waiting |

### OEA 2 · Investigate and resolve (`workflows/2-investigate-and-resolve.json`)
| Stage | Steps |
|---|---|
| Claim | **Claim exception**, then *Not already handled?* (two runs never work the same case) |
| Gather evidence | **Which systems to check?** routes by problem type: stock (each SKU + delivery promise), supplier and delivery date, payment, or pricing. Then **Look up past cases** |
| Diagnose | **Build Claude request**, then *Claude switched on?* If yes, **Ask Claude** (Anthropic API, forced `submit_resolution` tool) and **Read Claude's answer**. If Claude is off, unreachable or answers badly, **Rule playbook**. Then **Check and record diagnosis** (validates, re-prices every option from live data) |
| Decide | **Apply approval policy**: Code node, rules from `agent/policy.json`, same answers as `agent/policy.py` (tested). Then *Hold the order first?* (fraud, wrong price), **Hold order**, **Save options and decide** |
| Act | *Policy allows auto-fix?* If yes, **Fix it automatically**. If not, *Slack webhook set?* → **Ping the ops channel**, then **Wait for a person** (paused until the dashboard's Approve or Reject), then *Approved?* → **Run the approved fix** or **Closed without action** |

## Run it

```bash
python scripts/dev.py          # starts services, the orchestrator and n8n; imports + activates the workflows
```

Open the dashboard at http://localhost:8001 and the n8n editor at http://localhost:5678. On the first visit n8n asks
you to create its owner account; that's your local n8n login.

- Needs **Node.js 20–24**. The first run downloads n8n 1.123.85 with `npx` (a few minutes). If `n8n` is already
  installed globally, that's used instead.
- n8n keeps its data in `.n8n-data/`. Workflows are re-imported only when the JSON files change, so edits you make in
  the n8n editor survive restarts until you rebuild.
- No Node? `python scripts/dev.py --python` runs the same process with the built-in Python engine.

### Using an n8n you already run (Docker, n8n Cloud, desktop)
1. In n8n: **Workflows → Import from file** for both JSON files, then switch both on.
2. Give n8n these environment variables: `OEA_API_URL` (where the orchestrator is, e.g. `http://host.docker.internal:8001`),
   `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, and optionally `ANTHROPIC_API_KEY`, `OEA_SLACK_WEBHOOK_URL`.
3. Start the backend with `OEA_N8N_URL=<your n8n URL> python scripts/dev.py`. It will use your n8n instead of starting one.

## Settings (`.env`)

| Variable | Used by | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | n8n + backend | Turns Claude on. Empty means the rule playbook diagnoses |
| `OEA_SLACK_WEBHOOK_URL` | n8n | Slack incoming-webhook URL for "needs a decision" pings. Empty means no ping |
| `OEA_DASHBOARD_URL` | backend | Link put in the Slack message (default `http://localhost:8001`) |
| `OEA_ENGINE` | backend | `n8n` (default from `scripts/dev.py`) or `python` |
| `OEA_N8N_URL` | backend | Where n8n is (default `http://127.0.0.1:5678`) |
| `OEA_API_URL` | n8n | Where the orchestrator is (default `http://127.0.0.1:8001`) |
| `OEA_ANTHROPIC_URL` | n8n | Override the Anthropic endpoint (testing only) |

## Change a workflow

Edit `build_workflows.py` (not the JSON), then `python n8n/build_workflows.py`. Restarting `scripts/dev.py` re-imports.
Small tweaks can also be made in the n8n editor; export the workflow afterwards if you want to keep them in the repo.

## Test

| Command | Checks |
|---|---|
| `python -m pytest n8n/test_policy_parity.py` | n8n's policy Code node decides exactly like `agent/policy.py` (400 random cases) |
| `python -m pytest orchestrator/tests/test_n8n_bridge.py` | every bridge endpoint, called in the order the workflow calls them |
| `python n8n/e2e_check.py` | with the stack running: every scenario through real n8n, including approve-resume and reject-close |
| `python n8n/mock_anthropic.py 8099` + `OEA_ANTHROPIC_URL=http://127.0.0.1:8099` | the Claude path without an API key |
