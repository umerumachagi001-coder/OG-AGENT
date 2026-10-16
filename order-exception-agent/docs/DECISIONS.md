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
