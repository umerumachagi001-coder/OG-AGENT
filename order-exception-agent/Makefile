# Order Exception Agent — one-command targets (NFR-4).
# P1 targets are real; agent/dashboard/demo are placeholders for P2/P3/P4 to fill in.
PY ?= python3
PORT ?= 8000

.PHONY: install seed services test-services reset trigger agent dashboard demo

install:
	$(PY) -m pip install -r requirements.txt

seed:                       ## rebuild data/oea.db from scratch
	$(PY) -m data.seed

services:                   ## mock systems on http://localhost:$(PORT)  (docs at /docs)
	$(PY) -m uvicorn services.app:app --port $(PORT) --reload

test-services:              ## P1 end-to-end checks for SC-1..SC-4
	$(PY) -m pytest services/tests -q

reset:                      ## restore clean demo state on a running server
	curl -s -X POST localhost:$(PORT)/admin/reset

trigger:                    ## make trigger S=sc1   (sc1 | sc2 | sc3_expired | sc3_fraud | sc4)
	curl -s -X POST "localhost:$(PORT)/admin/trigger?fail=$(S)"

agent:
	@echo "TODO (P2): start the agent"

dashboard:
	@echo "TODO (P3): start the dashboard"

demo:
	@echo "TODO (P4): run the full demo stack"
