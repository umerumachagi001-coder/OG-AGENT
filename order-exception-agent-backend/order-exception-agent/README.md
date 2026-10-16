# Order Exception Agent

AI exception desk for order pipelines. When inventory, suppliers, payments, pricing or deadlines fail, the agent investigates the root cause, fixes low-risk issues automatically, and hands humans a one-click decision for the rest.

## Run it

```bash
make install        # Python deps + dashboard build (needs Python 3.11+, Node 18+)
cp .env.example .env   # optional: add ANTHROPIC_API_KEY for Claude; without it the agent uses rule playbooks
make demo           # then open http://localhost:8001
```

**Windows:** double-click `run-demo.bat` instead (installs packages, starts everything, opens the browser).

Click **Trigger a failure**, pick a scenario, and watch the case open. **Reset demo** restores a clean state.
Terminal walkthroughs: `make demo-sc1` … `make demo-sc4`. Tests: `make test`.

| Folder | What | Owner |
|---|---|---|
| `services/` | mock order systems (:8000) + `detector/` | P1, P4 |
| `agent/` | investigation loop, policy engine, fallback playbooks | P2 |
| `orchestrator/` | the API the dashboard uses (:8001) | P4 |
| `dashboard/` | React UI | P3 |
| `data/` | schema + seed | P1 |

## Working with Claude Code
- Open this folder in Claude Code — `CLAUDE.md` loads automatically.
- `/resume` — load context and get the next tasks
- `/checkpoint` — save progress before ending a session
- `/decision <text>` — log a design decision
- `/scope-check <idea>` — sanity-check a new feature against scope

## Docs
See `docs/` — problem statement, requirements (FR/NFR/SC IDs), architecture, plan, progress, decisions, demo script.
