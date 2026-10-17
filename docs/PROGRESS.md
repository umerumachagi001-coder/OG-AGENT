# Progress Log

> Update at the end of every session (or run `/checkpoint`). Newest entry on top.
> Keep each entry short — this is what the next session reads first.

## Current status
- **Phase:** rebuilt as an n8n automation (ADR-009). `python scripts/dev.py` starts n8n + backend + dashboard; every scenario passes through real n8n
- **Next up:** fill team names (README, deck); rehearse `docs/DEMO_SCRIPT.md` ×3 (now with voice + PDF) and record the backup video; optional: real `ANTHROPIC_API_KEY`, working Sarvam key for Hindi, Slack webhook
- **Blockers:** Claude path tested only against a stand-in Anthropic server. Names for P1–P4 and organiser questions still open

---

## 2026-10-17 (evening) — Browser voice with conversation, audit PDF (ADR-015) · `safe-point-3`
- Sarvam key rejected by Sarvam (403); `scripts/check_sarvam.py` confirmed the key itself was the cause. Voice now works without a key (browser speech, English) via `POST /voice/text`, with follow-up questions and context ("yes", "why", "next one", "approve it", "no thanks"). Sarvam stays optional with automatic fallback.
- Audit trail → **Download PDF** (`dashboard/src/auditReport.js`): summary, per-case sections, action log; follows the filter.
- 6-slide PowerPoint deck made (solution, architecture, authorization framework, data flow, dashboard, 24-hour MVP).
- n8n owner login reset (`npx.cmd n8n user-management:reset`). 52 tests pass; both features confirmed on the Windows laptop. Commit `098b7bb`, tag `safe-point-3`.

## 2026-10-17 — Hackathon day: repo, Supabase live, voice assistant (ADR-014)
- GitHub repo cleaned up: complete project at the root, old copies removed (tags `safe-point-1`, `safe-point-2`). Parity test fixed for Windows encoding. 45 tests passed on the Windows laptop.
- Supabase live check passed on the real project: sign-in with a real account, case records copied.
- Voice assistant (Sarvam, English + Hindi): `orchestrator/voice_api.py`, `dashboard/src/components/VoiceAssistant.jsx`. Read-only; opens cases, never approves. 50 tests; browser-tested with a simulated mic and Sarvam stand-in; n8n e2e passes.
- Known small race: if n8n's 10 s scan catches a triggered failure first, Trigger shows "already open". Harmless; follow-up.

## 2026-10-16 — Ops-lead walkthrough (exploratory test as a user)
- Played a full shift on the n8n stack with sign-in on: 4 failures at once, triage, fraud rejected, pricing typo then fix, two screens approving together, undo with double-clicks, reset while n8n waited, session expiry, backend outage + recovery, phone width, plus API misuse and race probes.
- Fixed: simultaneous Undo reversed the stock move 3× (now one at a time per case + services undo takes the write lock); a late Reject rewrote a resolved case; n8n mode accepted invalid/empty edited steps; a failed option (e.g. mistyped price) became a dead end; Reset demo briefly returned 500s while tables were rebuilt (now built aside and swapped in one step); n8n-mode failure message lacked the reason; a bad case link showed raw JSON.
- Held up: duplicate trigger, search, unknown ids/junk input (clean 4xx), 3 simultaneous approvals (one supplier switch), session expiry → sign-in, outage banner + recovery, phone layout.
- 45 tests (+4 regression); n8n e2e passes.

## 2026-10-16 — Supabase sign-in + case records (ADR-013)
- `supabase/schema.sql` (run in the SQL Editor): `oea_*` tables keyed by run, RLS read-only for signed-in users, `oea_case_overview` view. Tested on Postgres 16 with Supabase roles.
- Backend: `orchestrator/supabase_integration.py` (token check, background copy), `orchestrator/auth_api.py` (`/auth/config`, `/auth/me`, `/supabase/status`, `/supabase/sync`, login middleware). Dashboard: sign-in screen, token on every call, signed-in name on decisions, sign out.
- Verified with a stand-in Supabase on real Postgres: sign-in, wrong password, SC-5, SC-2 approve via n8n, undo, reload, sign out; rows and names correct in Postgres. 41 tests; n8n e2e passes with Supabase off.
- To do: paste keys into `.env`, run the SQL, create accounts, first live check.

## 2026-10-16 — SC-5 multi-failure (ADR-012)
- `sc5`: ORD-1001 phantom stock with only 3 days left on the promise. One high-severity case; playbook moves stock to WH-B **and** upgrades to Express (₹470, auto). Undo reverses both. Playbook now checks the delivery promise instead of assuming it.
- Fixed: case page showed the previous case's timeline after a demo reset reused the case number; delivery delay rounded 1.5 days up to 2.
- 36 Python tests; n8n e2e covers SC-1…SC-5 + undo.

## 2026-10-16 — Undo for automatic fixes (FR-15) + fraud severity (ADR-011)
- `POST /exceptions/{id}/undo` reverses an automatic fix (stock move, shipping upgrade, hold) and hands the case to a person; refused with a reason when the fix can't be taken back (email sent, payment captured). Dashboard: "Undo this fix" with confirm dialog, undone banner, timeline/audit/approvals entries.
- Detector rates gateway-flagged fraud declines higher: SC-3b is now **high** (was medium).
- Verified: 32 Python tests (+3), n8n e2e all pass, browser run of undo → re-approve on the n8n stack.

## 2026-10-16 — OrderGuard AI UI redesign (ADR-010)
- Frontend only: new shell (sidebar, search, n8n status), overview with order-network ring, exceptions table, investigation screen, approval center with confirm dialogs, workflow monitor, audit trail, supplier risk, order page.
- Verified on the live n8n stack in a browser: approve (n8n resumes → resolved), reject-all (case closed, n8n run ended), search, filters, every route, backend-down banner, phone width. No console errors. 29 Python tests + n8n e2e pass.
- Fixed during testing: query routes, polling a missing case, clustered ring labels, workflow arrows, audit times without seconds.

## 2026-10-16 — n8n automation
- Two workflows in `n8n/workflows/` (generated by `n8n/build_workflows.py`): detect (10 s schedule + `oea-scan` webhook) and investigate-and-resolve (37 nodes: per-domain evidence, Claude via API with rule-playbook fallback, policy Code node, hold, auto-fix or Slack ping + Wait node, resume on approval).
- `orchestrator/n8n_bridge.py`: `/n8n/*` endpoints; orchestrator `OEA_ENGINE=n8n` hands triggers to n8n and resumes paused runs on Approve/Reject; reset closes paused runs.
- Verified on real n8n 1.123.85: `n8n/e2e_check.py` all scenarios pass from a clean one-command start; Claude path via stand-in server (incl. cost correction ₹50→₹120) and Claude-unreachable fallback.
- Fixes found while testing: Split Out field name, bridge 500 on bad input, launcher readiness (wait for workflows to be active, start orchestrator before n8n), orphaned waits on reset. n8n install needed `xlsx` overridden (its CDN is blocked here; not an issue on normal networks).
- 29 Python tests (adds bridge + JS/Python policy parity).

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
