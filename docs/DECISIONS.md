# Decision Log (ADRs)

> Append-only. One entry per decision. Use `/decision` to add one.

Template:
```
## ADR-XXX — <title>  (YYYY-MM-DD)
**Context:** why this came up
**Decision:** what we chose
**Alternatives:** what we rejected and why
**Consequences:** what this commits us to
```

---

## ADR-001 — Solution approach: AI exception-desk agent  (2026-10-15)
**Context:** Brief asks how to handle order failures that currently need manual investigation.
**Decision:** Build an LLM agent with tools over (mocked) order systems, plus a policy engine for human-in-the-loop.
**Alternatives:** Pure rules engine (can't investigate novel cases); pure dashboard (doesn't remove manual work).
**Consequences:** Need tool contracts, guardrails and visible reasoning to earn judge trust.

## ADR-002 — Mock all backend systems  (2026-10-15)
**Context:** No real ERP/payment access in a hackathon.
**Decision:** FastAPI mocks with `?fail=<scenario>` flags and seeded SQLite data.
**Alternatives:** Public sandbox APIs (setup time, flaky during demo).
**Consequences:** Demo is deterministic; must make data realistic.

## ADR-003 — Default stack  (2026-10-15)
**Decision:** Python/FastAPI, SQLite, Claude API tool calling, React+Vite+Tailwind (Streamlit fallback).
**Revisit if:** organisers mandate a stack.

## ADR-004 — Team structure and timeline for 30 h / 4 people  (2026-10-15)
**Context:** Confirmed 30-hour event, team of 4.
**Decision:** Split by component (P1 Backend & Data, P2 Agent & Policy, P3 Dashboard, P4 Detector/Integration/Demo). Freeze contracts at H2, SC-1 end-to-end by H18, code freeze at H26.
**Alternatives:** Split by scenario (each person owns one end-to-end) — rejected: four people editing the agent and UI at once causes merge conflicts.
**Consequences:** Stubs must exist at H2 so parallel work never blocks; P4 is the integration owner.

## ADR-005 — Mock systems as one FastAPI app; scenarios mutate data  (2026-10-15)
**Context:** Six mock systems and a `?fail=` convention. Separate processes per system add ports and startup steps; a per-request `?fail=` flag only breaks one response, so the detector, agent and dashboard would each see a different world.
**Decision:** One FastAPI app (`services/app.py`, port 8000) with a router per system, sharing one SQLite file. Scenario failures are triggered once via `POST /admin/trigger?fail=sc1…` and change the source data, so every component sees the same broken order. Per-request `?fail=timeout|error|slow` remains on all read endpoints for testing error handling. Action endpoints enforce physical/business reality (stock, margin floor, expired card) but not approval policy.
**Alternatives:** One service per system (more realistic, more moving parts in a 30 h build); response-only fail flags (inconsistent state across components).
**Consequences:** `make services` is the only backend command. The demo reset is `POST /admin/reset`. The policy engine (P2) owns all approve/escalate logic.

## ADR-006 — Options are step lists; runner, not LLM, owns costs and execution  (2026-10-15)
**Context:** SC-2 can only be fixed by two actions together (switch supplier *and* expedite); SC-3b/SC-4 need an immediate hold whatever the human decides. The LLM could also misstate costs and slip under a policy threshold.
**Decision:** An option is `{title, steps[], cost, risk, impact, rationale}`; a separate `containment[]` list holds protective steps (only those in `containment_allowed`, i.e. `hold_order`). The LLM proposes only — it gets read-only tools. The runner re-computes every step's cost from live data before the policy check and logs any mismatch. Structured output is a forced `submit_resolution` tool call, validated; invalid output, API errors or timeouts drop to rule-based playbooks with the same output shape.
**Alternatives:** One action per option (can't express SC-2); letting the LLM call action tools (no guardrail).
**Consequences:** `proposed_actions.action_type` can be `a+b`; `params_json.steps` is what gets executed. Dashboard renders steps per option. Rules mode is a full no-API-key demo path.

## ADR-007 — Separate orchestrator process; detector auto-triggers the agent  (2026-10-15)
**Context:** The dashboard needs one backend for the queue, trail, approvals, metrics and demo controls. The agent calls the mock services over HTTP, so running it inside the services process risks it waiting on itself.
**Decision:** `orchestrator/app.py` on port 8001 wraps `agent.runner` and the detector, and proxies scenario/reset/orders calls to services. A background thread runs the detector every 2 s and investigates new exceptions automatically in a 3-worker pool; `/trigger` also runs detection immediately. Seeded history exceptions carry `origin='history'`: counted in metrics, hidden from the queue.
**Alternatives:** Mount everything in the services app (self-calls, deadlock risk); manual "Investigate" click only (slower, less impressive demo).
**Consequences:** `make demo` starts two processes via `scripts/dev.py` (cross-platform). Dashboard uses only `http://localhost:8001`. The demo flow needs no clicks between "Trigger failure" and the result.

## ADR-008 — Dashboard served by the orchestrator; amber reserved for human decisions  (2026-10-15)
**Context:** Demo day needs one command and no wifi dependency; judges watch on a projector.
**Decision:** React+Vite+Tailwind as planned (ADR-003), built to `dashboard/dist` and mounted by the orchestrator at `/ui` (`/` redirects there), so `make demo` → open `localhost:8001`. Fonts are bundled npm packages, not Google Fonts. Light theme for projector contrast. Amber + hazard stripe is used only where a person must act; the root cause is the page headline. Trail summaries are plain language (generated in `agent/llm.py::_summary`).
**Alternatives:** Streamlit (faster, but no live per-step trail or custom decision UI); separate Vite server on demo day (two URLs, CORS, one more thing to fail).
**Consequences:** After UI changes, run `make dashboard-build` (or `make install`) before `make demo`. `scripts/dev.py` now traps SIGTERM so stopping it never leaves orphan servers on 8000/8001.

## ADR-009 — n8n runs the exception process  (2026-10-16)
**Context:** The project should be an automation, built as n8n workflows that judges can see and edit, not a process hidden inside Python.
**Decision:** Two n8n workflows own the process: detection (schedule + webhook) and investigate-and-resolve (claim → per-domain evidence → Claude via the Anthropic API, rule playbook as fallback → policy in an n8n Code node → hold → auto-fix or Slack ping + Wait node → resume on approval). Python keeps the mock company systems and a thin `/n8n` bridge for what must be shared: the trail the dashboard shows, the prompt/schema, cost verification from live data, and executing actions. The dashboard's Approve resumes the paused n8n run through its resume URL. Workflows are generated from `n8n/build_workflows.py`; the policy Code node is tested against `agent/policy.py` for identical answers. Pinned to n8n 1.123.85 (last 1.x; runs on Node 20–24, unlike n8n 2.x which needs Node 24).
**Alternatives:** n8n only wrapping the Python agent (little visible in n8n); everything in n8n incl. the mock systems (throws away the tested data layer and scenarios); n8n's AI Agent node with tools (needs credentials set up in the n8n UI and is harder to test offline).
**Consequences:** `python scripts/dev.py` starts n8n too (npx or an installed n8n), importing workflows when they change. `OEA_ENGINE=python` keeps the old engine as a demo fallback. Claude's investigation is a single structured call over evidence n8n gathered, rather than Claude choosing its own tool calls.

## ADR-010 — OrderGuard AI dark operations UI, frontend only  (2026-10-16)
**Context:** Team supplied three visual references (premium analytics, circular supplier-risk view, workflow editor) and a brief requiring a multi-section command center without fabricated data or backend changes.
**Decision:** Rebuilt `dashboard/src` as six sections (overview, exceptions, investigation, approvals, workflow, audit, suppliers) on a navy palette with cyan reserved for the interface and fixed status colours paired with labels. The circular visual is an order network of the 50 real orders, not suppliers: only 4 suppliers exist, so suppliers get a dot plot and table. Workflow stage states are derived from each case's trail; the n8n editor link covers execution history (the n8n API needs a key). Supplier, PO and action-log views read the mock systems on :8000 directly (already CORS-open) instead of adding orchestrator routes. No backend, API or n8n changes.
**Alternatives:** Proxy endpoints on the orchestrator (cleaner single origin, but a protected backend change); a decorative globe or supplier ring (would need invented data).
**Consequences:** The dashboard needs ports 8000 and 8001 reachable from the browser; if :8000 is down, suppliers and the system log show an error state while the rest keeps working. Light theme from ADR-008 replaced by dark.

## ADR-011 — Undo for automatic fixes (FR-15); fraud flag raises severity  (2026-10-16)
**Context:** Judges will ask "what if the agent gets it wrong?". FR-15 asks for rollback of auto-executed actions. Separately, SC-3b fraud cases were rated medium because the detector ignored the gateway's fraud flag.
**Decision:** Every action response now carries the `action_log` id plus the state it replaced (`previous_status`, `previous_carrier`). `POST /actions/undo {log_id}` on the services reverses one logged action, refusing when the order has moved on, it was already undone, or the action is irreversible (an email sent, a captured payment). The runner's `undo(exception_id)` reverses the automatic fix newest step first and hands the case to a person: every option becomes a pending proposal needing approval and the resolution is withdrawn. Orchestrator: `POST /exceptions/{id}/undo`; `GET /exceptions/{id}` includes `undo: {possible, reason, steps}`. Dashboard shows "Undo this fix" with a confirm dialog on automatically fixed cases. Undo is never offered to the LLM (not in `ACTIONS`). The detector adds +2 severity when the latest decline is `suspected_fraud` or its fraud score is above 0.5.
**Alternatives:** A third n8n workflow for undo (more visible, but the automatic run has already finished, so it adds moving parts for no behaviour change); compensating actions through the normal action endpoints (reallocating back would fail the verified-stock check, which is right for a new move but wrong for a restore).
**Consequences:** Undo runs in Python in both engines. Only reallocate stock, upgrade shipping and hold are reversible; the dashboard says why when a fix can't be undone. Fixes executed before this change can't be undone (no log id).

## ADR-012 — SC-5: one case per order, combined fix for stock + deadline  (2026-10-16)
**Context:** Stretch scenario SC-5 is an order failing two ways at once (inventory + deadline). Two separate cases for one order would mean two investigations proposing conflicting fixes.
**Decision:** The detector keeps one case per order: a pick failure whose promise is also at risk is a single inventory case with severity +1 and the deadline in the symptom. The inventory playbook now checks the delivery promise after finding stock: if the current carrier is late it proposes moving the stock **and** the cheapest carrier that meets the promise as one option (step list, ADR-006), with the next-fastest as option 2. If no carrier can make it, it proposes shipping and telling the customer. The playbook no longer claims the promise is met without checking.
**Alternatives:** A separate deadline case linked to the inventory case (needs case linking in the schema and UI); a new demo order ORD-1005 (would shift the seeded background orders and every count that depends on them).
**Consequences:** SC-5 reuses ORD-1001 (like SC-3a/3b share ORD-1003), so trigger SC-1 or SC-5, not both, between resets. SC-5 auto-resolves for ₹470 within policy; its undo reverses both steps.

## ADR-013 — Supabase for sign-in and a copy of the case records  (2026-10-16)
**Context:** The team wants Supabase for data storage and authentication, with the secret key kept secure. The demo must still run offline and "Reset demo" must keep rebuilding the mock company systems.
**Decision:** Supabase Auth protects the whole dashboard; accounts are created by the project owner (no sign-up). The dashboard signs in through Supabase Auth's REST API (no new dependency) using the publishable key, which the backend serves from `GET /auth/config`; every orchestrator call carries the access token, checked by the backend against `GET /auth/v1/user` (cached 60 s). Decisions are recorded against the signed-in person. Case records (exceptions, trail, options, resolutions, action log, notifications) are copied from SQLite to `oea_*` tables every 3 s by a background thread using the secret key on the `apikey` header; rows are keyed by (run, id) so restarted case numbers after a reset never overwrite earlier runs. RLS: signed-in users read, nobody writes through the API. All of it lives in `orchestrator/supabase_integration.py` and `orchestrator/auth_api.py`; with no keys set nothing changes.
**Alternatives:** Moving every table to Supabase (rewrite of the data layer; demo dependent on internet; Reset would wipe Supabase); supabase-js in the dashboard (extra dependency for three REST calls); verifying JWTs locally (Supabase's new asymmetric keys need JWKS handling; one cached call is simpler).
**Consequences:** Scripts that call the API without a browser need `OEA_REQUIRE_LOGIN=0`. The mock systems on :8000 and n8n's `/n8n/*` bridge stay unauthenticated but only listen on 127.0.0.1. Tested with a simulated Supabase and with real Postgres 16 running `supabase/schema.sql`; not yet against the live project (no key in this environment).
