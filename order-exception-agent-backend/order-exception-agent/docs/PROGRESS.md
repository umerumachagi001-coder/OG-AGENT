# Progress Log

> Update at the end of every session (or run `/checkpoint`). Newest entry on top.
> Keep each entry short — this is what the next session reads first.

## Current status
- **Phase:** all four roles have a working first version. Full demo runs from `make demo` → `http://localhost:8001` in rules mode
- **Next up:** real `ANTHROPIC_API_KEY` run + prompt tuning to 5/5 per scenario; rehearse with `docs/DEMO_SCRIPT.md`; record backup video; pitch deck; consider raising severity for fraud signals (currently medium)
- **Blockers:** LLM path untested with a real key. Names for P1–P4 and organiser questions still open

---

## 2026-10-15 — First run on a teammate's Windows laptop
- Ran from `Documents\order-exception-agent` via new `run-demo.bat`; SC-1 auto-resolved, SC-2 approved end to end.
- Bug found and fixed: on Windows `localhost` tries IPv6 first → ~2 s stall per new connection; case view piled up requests and never loaded. Now `127.0.0.1` everywhere server-side, one pooled client in the orchestrator, dashboard skips a poll while the previous one is in flight.

## 2026-10-15 — P3 dashboard built
- `dashboard/`: queue, case view with root-cause headline, live trail, decision panel with approve / edit / reject, trigger + reset, metrics line, draft customer message (FR-13, 14, 16–19). Served at `localhost:8001`. ADR-008.
- Verified in a real browser against the live stack (SC-1, SC-2 approve, SC-3 fraud, SC-4 edit) at 1440 px and 390 px; no console errors.
- Agent trail summaries rewritten in plain language. Fixed: launcher left orphan servers on `kill`.

## 2026-10-15 — P4 detector + orchestrator built
- `services/detector/rules.py`: 6 rules, dedup, severity scoring (FR-1..4). Healthy seed → 0 exceptions; each scenario → exactly 1.
- `orchestrator/app.py` (:8001): queue, detail, incremental trail, investigate, approve/modify/reject, metrics (FR-19), proxied trigger/reset/orders. Background detect + auto-investigate loop. ADR-007.
- `make demo` starts everything; `make demo-sc1…sc4` prints the judge-facing run. Live: SC-1 auto-resolved 0.6 s; SC-2 escalated 0.5 s, approved → resolved.
- `make test` → 24 tests (9 services, 10 agent, 5 full-stack).

## 2026-10-15 — P2 agent & policy built
- `agent/`: tool registry over the mock APIs (FR-5), Claude tool loop with forced `submit_resolution` + validation (FR-6, FR-9), trail logger (FR-7), policy engine from `policy.json` (FR-10/11, NFR-2), rule-based fallback playbooks for all scenarios (NFR-5), runner `investigate/approve/reject` (FR-13, FR-20). ADR-006.
- Rules mode resolves all scenarios as expected; SC-1 auto-resolves end to end in under a second.
- Policy additions: `notify_customer`/`hold_order` auto, `fraud_score_above`, `option_risk: high`, `max_option_cost`, `containment_allowed`.
- `make test` → 19 tests pass (9 services, 10 agent incl. fake-Claude loop, fallback, step budget, cost-check).
- CLI until detector exists: `python -m agent dev ORD-1001 inventory "Pick failed"`.


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
