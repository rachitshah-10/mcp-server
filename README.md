# IT Equipment Request Handler (MCP Server + ReAct Agent)

An internal IT equipment request system built with the **Model Context Protocol (MCP)** Python SDK, **SQLite**, and a **ReAct + Reflection** agent powered by **Azure AI Foundry (`gpt-6-astra`)**.

See [REQUIREMENTS.md](REQUIREMENTS.md) for the complete policy matrix, escalation rules, and decision boundaries, and [PRODUCT_PLAN.md](PRODUCT_PLAN.md) for the detailed product specification.

---

## Architecture

```text
  ReAct + Reflection Agent (agent.py, llm_client.py, mcp_client.py)
            │
            │  JSON-RPC over stdio (MCP)
            ▼
  mcp_server.py       — Thin FastMCP/MCPServer tool wrappers (6 tools)
            │
            ▼
  service.py          — Pure business logic, eligibility engine, validation
            │
            ▼
  db.py + seed.py     — SQLite schema (8 tables, 4 indexes) & deterministic seed
```

### Exposed MCP Tools (`src/it_equipment_handler/mcp_server.py`)
1. `get_employee_info(employee_id)` — profile (`role`, `department`, `work_mode`, `status`, `tenure_months`, `manager_id`), dispatch history, tickets, and live annual department budget.
2. `get_policy_limits(role)` — structured policy limits for that role plus natural-language policy clauses and reviewer directory.
3. `check_request_eligibility(employee_id, item)` — deterministic eligibility check across status, catalog ontology, role/work mode, tenure, dependencies, open tickets, refresh cooldown, auto-approval cost cap, and dynamic annual department budget.
4. `flag_for_human_review(employee_id, request, reason, reviewer_employee_id, category=None, resource_id=None)` — creates an escalated ticket assigned to the chosen human reviewer (`E901` IT-Ops, `E902` HR, `E903` InfoSec, or `employee.manager_id`).
5. `lookup_resource(query)` — resolves product names, categories, and aliases via escaped SQL search + ranking.
6. `finalize_request_decision(employee_id, request_text, decision, reason, resource_id=None)` — post-reflection persistence for `APPROVED` (atomic log + auto-dispatch) and `DENIED` (log only).

---

## Local Setup (Python 3.12)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Copy `.env.example` to `.env` (gitignored) and configure your Azure AI Foundry key and token rates:

```bash
cp .env.example .env
```

```env
AZURE_OPENAI_ENDPOINT=https://foundry-apps-01.services.ai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-6-astra
AZURE_OPENAI_INPUT_COST_PER_1M=<rate>
AZURE_OPENAI_OUTPUT_COST_PER_1M=<rate>
IT_EQUIPMENT_DB_PATH=.local_equipment.db
```

---

## Running Tests & Coverage

```bash
.venv/bin/pytest -v
```

With coverage (`pytest-cov`):

```bash
.venv/bin/pytest --cov=it_equipment_handler --cov-report=term-missing
```

---

## Running the Demo Agent & MCP Checks

- **Minimal 1-tool smoke check:**
  ```bash
  .venv/bin/python -m it_equipment_handler.smoke_client
  ```
- **Direct MCP `get_employee_info` client check:**
  ```bash
  .venv/bin/python -m it_equipment_handler.client_check
  ```
- **Run all 5 live ReAct + Reflection demo scenarios (`approve_laptop`, `deny_contractor`, `escalate_budget`, `escalate_damage_conflict`, `escalate_ergonomic`):**
  ```bash
  .venv/bin/python -m it_equipment_handler.demo
  ```
- **Run a single scenario:**
  ```bash
  .venv/bin/python -m it_equipment_handler.demo --scenario approve_laptop
  ```

---

## Docker Usage

Build the container image and run the test suite inside Docker:

```bash
docker build -t it-equipment-handler:dev .
docker run --rm it-equipment-handler:dev pytest -v
```

Run the live agent inside Docker using your local `.env` at runtime (secrets are never baked into the image):

```bash
docker run --rm --env-file .env it-equipment-handler:dev python -m it_equipment_handler.demo --scenario approve_laptop
```

---

## CI Pipeline

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs automatically on pushes and pull requests to `main`:
1. **Python 3.12 Unit Tests:** Installs `.[dev]` and runs `pytest -v`.
2. **Docker Build & Container Tests:** Builds `Dockerfile` and runs `pytest -v` inside the container.
