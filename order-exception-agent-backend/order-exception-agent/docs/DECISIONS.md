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
