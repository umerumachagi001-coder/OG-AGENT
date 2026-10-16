# Order Exception Agent — one-command targets (NFR-4).
# Start everything: make demo   ·   run a scenario: make demo-sc1   ·   all tests: make test
PY ?= python3
PORT ?= 8000

.PHONY: install seed services orchestrator dashboard-build test-services test-agent test-orchestrator test reset trigger agent dashboard demo demo-sc1 demo-sc2 demo-sc3 demo-sc3-fraud demo-sc4

install:                    ## Python deps + dashboard deps + dashboard build
	$(PY) -m pip install -r requirements.txt
	cd dashboard && npm install && npm run build

seed:                       ## rebuild data/oea.db from scratch
	$(PY) -m data.seed

services:                   ## mock systems on http://localhost:$(PORT)  (docs at /docs)
	$(PY) -m uvicorn services.app:app --port $(PORT) --reload

test-services:              ## P1 end-to-end checks for SC-1..SC-4
	$(PY) -m pytest services/tests -q

reset:                      ## restore clean demo state on a running stack
	curl -s -X POST localhost:8001/reset

trigger:                    ## make trigger S=sc1   (sc1 | sc2 | sc3_expired | sc3_fraud | sc4)
	curl -s -X POST "localhost:8001/trigger?fail=$(S)"

test-agent:                 ## P2 agent tests (rules path + scripted fake Claude)
	$(PY) -m pytest agent/tests -q -p no:warnings

test-orchestrator:          ## P4 full-stack tests (API → detector → agent → services)
	$(PY) -m pytest orchestrator/tests -q -p no:warnings

test:                       ## all 24 tests in one run
	$(PY) -m pytest services/tests agent/tests orchestrator/tests -q -p no:warnings

orchestrator:               ## orchestrator API only, on :8001 (needs services running)
	$(PY) -m uvicorn orchestrator.app:app --port 8001 --reload

agent:                      ## make agent ORDER=ORD-1001 DOMAIN=inventory SYMPTOM="Pick failed"  (dev until detector exists)
	$(PY) -m agent dev $(ORDER) $(DOMAIN) "$(SYMPTOM)"

dashboard:                  ## dashboard with hot reload on :5173 (needs `make demo` running)
	cd dashboard && npm run dev

dashboard-build:            ## rebuild the dashboard that `make demo` serves at :8001
	cd dashboard && npm run build

demo:                       ## start everything; open http://localhost:8001
	$(PY) scripts/dev.py

demo-sc1:                   ## run a scenario end to end against `make demo`
	$(PY) scripts/demo_scenario.py sc1
demo-sc2:
	$(PY) scripts/demo_scenario.py sc2 --approve
demo-sc3:
	$(PY) scripts/demo_scenario.py sc3_expired
demo-sc3-fraud:
	$(PY) scripts/demo_scenario.py sc3_fraud
demo-sc4:
	$(PY) scripts/demo_scenario.py sc4
