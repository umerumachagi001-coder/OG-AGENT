#!/usr/bin/env bash
# Re-import the workflows into a local n8n (dev helper). Stop n8n first; start it again afterwards.
set -e
cd "$(dirname "$0")"
python3 build_workflows.py
for f in workflows/*.json; do npx n8n import:workflow --input="$f"; done
npx n8n update:workflow --id=oeaDetect0000001 --active=true
npx n8n update:workflow --id=oeaInvestigate01 --active=true
