# Backend: file guide

Everything except `dashboard/`. Python 3.11+, FastAPI, SQLite. About 3,800 lines.

## Run

```bash
pip install -r requirements.txt
python scripts/dev.py          # Windows: double-click run-demo.bat
python -m pytest services/tests agent/tests orchestrator/tests -q
```

| Port | Process | What |
|---|---|---|
| 8000 | `services/app.py` | mock order systems (API docs at `/docs`) |
| 8001 | `orchestrator/app.py` | the API the dashboard calls, plus the built dashboard at `/` |

## How a failure flows through the files

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
| `scenarios.py` | The failures: `sc1` phantom stock, `sc2` supplier delay, `sc3_expired`, `sc3_fraud`, `sc4` discount stacking |
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

### `scripts/` and root
| File | Purpose |
|---|---|
| `scripts/dev.py` | Starts both servers, cross-platform, stops both cleanly |
| `scripts/demo_scenario.py` | Prints one scenario's run in the terminal: `python scripts/demo_scenario.py sc2 --approve` |
| `run-demo.bat` | Windows one-click: install, start, open browser |
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

Full endpoint tables and data shapes: `docs/ARCHITECTURE.md`. Design decisions: `docs/DECISIONS.md`.
