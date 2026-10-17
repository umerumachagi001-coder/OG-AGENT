# Backend: file guide

Everything except `dashboard/`. **n8n runs the process** (`n8n/`); Python provides the company systems and the bridge n8n calls. Python 3.11+, FastAPI, SQLite; n8n 1.123.85 on Node 20–24.

## Run

```bash
pip install -r requirements.txt
python scripts/dev.py          # starts n8n too; Windows: double-click run-demo.bat
python -m pytest services/tests agent/tests orchestrator/tests n8n/test_policy_parity.py -q
```

| Port | Process | What |
|---|---|---|
| 5678 | n8n | the two workflows that run the process (editor at this address) |
| 8000 | `services/app.py` | mock order systems (API docs at `/docs`) |
| 8001 | `orchestrator/app.py` | the API the dashboard calls, the `/n8n` bridge, and the built dashboard at `/` |

## How a failure flows (n8n engine, the default)

```
POST /trigger?fail=sc1            orchestrator/app.py → services break the data → n8n webhook oea-scan
  n8n "OEA 1 · Detect exceptions"     → POST /n8n/scan (services/detector/rules.py)
  n8n "OEA 2 · Investigate and resolve"
       → /n8n/exceptions/{id}/start, /tool ×N        orchestrator/n8n_bridge.py → services/api_read.py
       → Ask Claude (api.anthropic.com) or /playbook  agent/fallback.py
       → /prepare (validate, verify costs)            agent/runner.py helpers
       → Apply approval policy (n8n Code node)        n8n/policy_engine.js  ≡ agent/policy.py
       → /decide → /actions/{id}/run  or  Wait for a person → dashboard Approve → resume → /run
```

## How a failure flows (Python engine, `--python`)

```
POST /trigger?fail=sc1            orchestrator/app.py
  → POST /admin/trigger           services/app.py → services/scenarios.py   (breaks the data)
  → detect_once()                 services/detector/rules.py                (creates the exception)
  → runner.investigate(id)        agent/runner.py
       ├ llm.investigate()        agent/llm.py      (Claude, read-only tools from agent/tools.py)
       └ fallback.run()           agent/fallback.py (if no API key or Claude fails)
       → policy.evaluate()        agent/policy.py + agent/policy.json
       → execute or escalate      agent/tools.py → services/api_actions.py
       → trail, actions, result   agent/store.py → SQLite
```

## Folder by folder

### `data/`: database (P1)
| File | Purpose |
|---|---|
| `schema.sql` | All tables: source systems (orders, inventory, suppliers, payments, promotions, carriers) and agent tables (exceptions, investigation_trail, proposed_actions, resolutions, action_log, notifications) |
| `seed.py` | Builds a clean, deterministic demo world: 50 orders, 20 customers, 3 warehouses, 4 suppliers, 12 past resolved cases. `python -m data.seed` |

### `services/`: mock order systems, port 8000 (P1)
| File | Purpose |
|---|---|
| `app.py` | FastAPI app; admin endpoints `/admin/scenarios`, `/admin/trigger`, `/admin/reset`, `/admin/action_log`, `/health` |
| `api_read.py` | Read endpoints, one per agent tool: orders, inventory, suppliers, payments, pricing, SLA, similar cases |
| `api_actions.py` | Action endpoints the agent executes: reallocate stock, switch supplier, retry payment, hold/release, price correction, expedite, notify. Each refuses physically impossible moves |
| `logic.py` | Pricing rules (promo stacking, margin floor, correct price) and delivery-date maths |
| `scenarios.py` | The failures: `sc1` phantom stock, `sc2` supplier delay, `sc3_expired`, `sc3_fraud`, `sc4` discount stacking, `sc5` phantom stock + tight deadline |
| `common.py` | DB connection, time helpers, audit log, `?fail=timeout\|error\|slow` injection |
| `detector/rules.py` | Detector (P4): 6 rules that turn broken orders into exceptions, with dedup and severity scoring |
| `tests/test_services.py` | 9 tests: each scenario's trigger → investigate → act |

### `agent/`: investigation and decisions (P2)
| File | Purpose |
|---|---|
| `runner.py` | **Entry points**: `investigate()`, `approve()`, `reject()`. Re-checks costs from live data, runs containment, applies policy, executes or escalates |
| `llm.py` | Claude tool loop: system prompt, `submit_resolution` output schema, validation, step budget, plain-language trail summaries |
| `fallback.py` | Rule playbooks for every domain, same output shape as the LLM. Used without an API key or when Claude fails |
| `policy.py` | Decides auto vs needs-a-human for each option, with reasons |
| `policy.json` | **The editable rules**: cost limits, always-escalate actions, VIP/value/confidence/fraud triggers |
| `tools.py` | Tool schemas given to Claude, HTTP client to the services, action catalogue |
| `store.py` | Reads and writes exceptions, trail steps, proposed actions, resolutions |
| `config.py` | Settings from env vars: services URL, mode, model, step and time limits |
| `__main__.py` | CLI: `python -m agent dev ORD-1001 inventory "Pick failed"`, `approve`, `reject`, `trail` |
| `tests/test_agent.py` | 10 tests incl. a scripted fake Claude, fallback on API error, cost correction |

### `orchestrator/`: API for the dashboard, port 8001 (P4)
| File | Purpose |
|---|---|
| `app.py` | Queue, case detail, live trail, approve/reject, metrics, trigger/reset proxy; background loop that detects and auto-investigates every 2 s; serves the built dashboard |
| `tests/test_orchestrator.py` | 5 full-stack tests through the API |

### `n8n/`: the automation (ADR-009)
| File | Purpose |
|---|---|
| `workflows/1-detect-exceptions.json` | Import into n8n. Schedule + webhook → detector → start one investigation per exception |
| `workflows/2-investigate-and-resolve.json` | Import into n8n. The whole investigate → decide → act/escalate process (37 nodes) |
| `build_workflows.py` | **Edit this**, then run it to regenerate the JSON |
| `policy_engine.js` | The policy Code node's source (rules read from `agent/policy.json`) |
| `README.md` | Step-by-step description, settings, how to use your own n8n |
| `test_policy_parity.py` | n8n policy ≡ Python policy on 400 random cases |
| `e2e_check.py` | Every scenario through real n8n against a running stack |
| `mock_anthropic.py` | Stand-in Claude API for testing without a key |
| `render_workflows.py` | Draws the workflows to PNG for slides |

### `orchestrator/n8n_bridge.py`
The `/n8n/*` endpoints the workflows call: claim, checks (logged to the trail), Claude request body, rule playbook, validate + cost check, hold, save options, execute. Table in `docs/ARCHITECTURE.md`.

### `scripts/` and root
| File | Purpose |
|---|---|
| `scripts/dev.py` | Starts services, orchestrator and n8n (imports + activates workflows when they change); `--python` skips n8n |
| `scripts/wait_ready.py` | Used by `run-demo.bat` to open the browser once everything is up |
| `scripts/demo_scenario.py` | Prints one scenario's run in the terminal: `python scripts/demo_scenario.py sc2 --approve` |
| `run-demo.bat` | Windows one-click: install, start n8n + backend, open dashboard and n8n editor |
| `Makefile` | `make install / demo / test / demo-sc1…` (macOS/Linux) |
| `requirements.txt` | fastapi, uvicorn, pydantic, httpx, pytest, anthropic |
| `.env.example` | Copy to `.env`; add `ANTHROPIC_API_KEY` to use Claude |

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (empty) | Set it to use Claude; empty means rule playbooks |
| `OEA_AGENT_MODE` | `auto` | `auto`, `llm` or `rules` |
| `OEA_MODEL` | `claude-sonnet-5-5` | Claude model |
| `OEA_MAX_TOOL_CALLS` | `8` | Investigation step budget |
| `OEA_LLM_TIMEOUT` | `25` | Seconds before falling back to rules |
| `OEA_SERVICES_URL` | `http://127.0.0.1:8000` | Keep `127.0.0.1`; `localhost` is slow on Windows |
| `OEA_ENGINE` | `n8n` | `n8n` or `python` |
| `OEA_SLACK_WEBHOOK_URL` | (empty) | Slack ping when a decision is needed |
| `OEA_N8N_URL` | `http://127.0.0.1:5678` | Use an n8n you already run |

Full endpoint tables and data shapes: `docs/ARCHITECTURE.md`. Design decisions: `docs/DECISIONS.md`.
