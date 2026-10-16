# Progress Log

> Update at the end of every session (or run `/checkpoint`). Newest entry on top.
> Keep each entry short — this is what the next session reads first.

## Current status
- **Phase:** 0→1 — P1 foundations done ahead of schedule
- **Next up:** P2 tool wrappers against `localhost:8000` (endpoint map in `docs/ARCHITECTURE.md`); P4 detector rules from the scenario table there; P3 can read `/orders`, `/admin/scenarios`, `/admin/trigger` now
- **Blockers:** none. Still open: names for P1–P4 in `docs/PLAN.md`, organiser questions

---

## 2026-10-15 — P1 backend & data built
- `data/schema.sql` + `data/seed.py`: 50 orders, 20 customers, 3 warehouses, 4 suppliers, 12 past resolved exceptions; deterministic (FR-1).
- `services/app.py`: one FastAPI app with orders, inventory, supplier, payment, pricing, SLA, similar-cases endpoints (FR-5, FR-8) and 7 action endpoints + release_order (FR-12). See ADR-005.
- Scenarios `sc1`, `sc2`, `sc3_expired`, `sc3_fraud`, `sc4` via `POST /admin/trigger?fail=`; `POST /admin/reset`; `?fail=timeout|error|slow` on reads.
- `make seed | services | test-services | reset | trigger S=sc1`. 9 tests pass, each walking a scenario trigger → investigate → act.
- Note for P2: in SC-2 switching supplier alone is still late — the agent must combine switch_supplier + EXPRESS. Retry on SC-3 cards fails by design.
- PLAN P1 Phase 1 + Phase 2 items + reset endpoint: done.

## 2026-10-15 — Constraints confirmed
- Hackathon is **30 hours**, team of **4**.
- Plan rewritten: 4 roles (Backend & Data, Agent & Policy, Dashboard, Detector/Integration/Demo), hour-by-hour phases, sync checkpoints, sleep rotation, code freeze at H26.
- Key milestone: SC-1 end-to-end by H18.

## 2026-10-15 — Project initialised
- Created project scaffold, CLAUDE.md, requirements (FR/NFR/SC IDs), architecture, plan.
- Chosen approach: AI exception-desk agent (detect → investigate → decide → act/escalate → learn).
- Open: team size, duration, required stack, judging criteria.
