# Lifeline starter (chip supply resilience)

One AI agent reads supplier messages from any channel, matches them to your orders and inventory,
calculates the exposure with plain code, and proposes options that a person approves.

## Run
    pip install -r requirements.txt
    cd backend
    python demo_cli.py            # full pipeline in the terminal, no server
    uvicorn main:app --reload     # dashboard at http://localhost:8000 (API docs at /docs)

## How it flows
channel (simulator / Twilio / email) -> `agent.ingest` -> `llm.extract` (AI understands)
-> match supplier + order -> `risk.exposure` + `scenarios.run_scenarios` (code calculates)
-> alert (`pending_approval` or `needs_review` if confidence < 0.6) -> human approves -> audit log.

## Files to know
- `data/*.csv`        your real data goes here (keep the column names), then delete lifeline.db or click Reset
- `backend/risk.py`   cover days, stock-out date, gap, severity, supplier score
- `backend/scenarios.py` do nothing / run slower / backup supplier / pay early; assumptions in `config.py`
- `backend/llm.py`    all AI calls; `LIFELINE_LLM=mock|anthropic|sap`
- `backend/agent.py`  the agent pipeline, approvals, grounded what-if (`ask`)

## Switch on a real model
    LIFELINE_LLM=anthropic ANTHROPIC_API_KEY=... uvicorn main:app --reload
    LIFELINE_LLM=sap       (after pip install generative-ai-hub-sdk and setting AICORE_* variables)
If a model call fails the app falls back to the rule-based mock, so the demo never breaks.

## Real channels (later)
Point a Twilio WhatsApp/SMS webhook at POST /webhooks/twilio, and forward email to POST /webhooks/email.
Add Twilio signature validation and authentication before exposing it publicly.

## Twilio (WhatsApp / SMS)
1. Put your own phone number (with country code, e.g. +91...) on one supplier in `data/suppliers.csv`, then click "Reset demo".
2. Make the server reachable (ngrok/Cloudflare Tunnel locally, or the BTP app URL once deployed).
3. In Twilio's sandbox settings set "When a message comes in" to `https://YOUR-URL/webhooks/twilio` (POST).
4. Optional hardening: set `TWILIO_AUTH_TOKEN` and `LIFELINE_PUBLIC_URL` (your https base URL, no trailing slash) to reject requests not signed by Twilio.

## Deploy on SAP BTP Cloud Foundry
    cf login -a <API endpoint> --sso
    cf push            # reads manifest.yml
    cf set-env lifeline LIFELINE_PUBLIC_URL https://<your-app-url>
    cf restage lifeline
SQLite lives on temporary disk there: data resets on restart and is re-seeded from data/*.csv. Move to HANA Cloud for persistence.

## Hosting without Cloud Foundry
- Render (free web service): Build `pip install -r requirements.txt`, Start `cd backend && python -m uvicorn main:app --host 0.0.0.0 --port $PORT`.
- Env vars: LIFELINE_DB=/tmp/lifeline.db, LIFELINE_LLM=mock (or sap + AICORE_*), LIFELINE_PASSWORD=<dashboard password>.
- LIFELINE_PASSWORD protects the dashboard/API with a browser login (any username); webhooks and /api/health stay open.

## Other AI providers (no SAP needed)
`LIFELINE_LLM=openai` talks to any OpenAI-compatible endpoint with no extra packages:
- LLM_BASE_URL (default http://localhost:11434/v1 = local Ollama), LLM_MODEL (default qwen2.5:3b), LLM_API_KEY (default none), LLM_TIMEOUT (seconds)
- Local small model: install Ollama, `ollama pull qwen2.5:3b`, then run the app on the same machine.
- Hosted providers: set the three LLM_* variables from the provider's docs.
- `LIFELINE_LLM=anthropic` (needs `pip install anthropic` and ANTHROPIC_API_KEY) and `LIFELINE_LLM=sap` also exist.
Model output is validated and cross-checked against the rule-based reader; disagreements go to "needs review".

## Delivery-complaints dataset
`python tools/convert_complaints.py <workbook.xlsx>` rebuilds data/*.csv from the complaints workbook (needs `pip install openpyxl`).
Real: carriers, consignment numbers, delay reasons/days, statuses, impacts, the company's actions per reason (data/playbook.csv).
Sample (generated, consistent): items, quantities, dates, stock, costs, contacts, alternate carriers.
Order references can be PO-1234 or consignment numbers like AIC-HYP-20261001.

## AI response plans
Dashboard > AI Assistant > "AI response plans": for each alert waiting on a decision the AI drafts actions, a message to the partner and
questions to ask, grounded in the verified numbers and data/playbook.csv. Without a model it falls back to rule-based plans.
Prompt: PLAN_SYSTEM in backend/llm.py. Plans are cached per alert (Regenerate to refresh). Nothing is sent automatically.

## Tunnel (Windows)
Run the server first, then:  powershell -ExecutionPolicy Bypass -File tools\start_tunnel.ps1
Prints the public link only after the tunnel is connected. Cloudflare error 1033 means the tunnel is not running/connected.

## Options vary per alert
backend/scenarios.py builds a different option set per alert: reason-specific carrier fixes (REASON_ACTIONS), express transport,
split shipment, re-sequencing, backup partner. "Run line slower" is hidden when it gives no benefit.
Free-form Ask: facts are collected in agent.ask_prepare and worded by the model (llm.answer); figures not in the data are flagged.

## One-command local start (Windows)
powershell -ExecutionPolicy Bypass -File .\run_local_ai.ps1   (checks Python, packages, Ollama, the model and the port, then starts Lifeline)
- Prompts: backend/prompts/plan_system.md is the AI response-plan prompt (restart the server after editing).
