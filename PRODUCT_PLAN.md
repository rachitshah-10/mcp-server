# Product Plan: IT Equipment Request Handler (MCP Server + ReAct Agent)

## 1. Product Overview

An internal IT equipment request system built from two components:
1. **MCP Server (`sqlite3`-backed):** Exposes company employee data, resource catalog/ontology, dispatch history, annual department budgets, hybrid (structured + natural-language) policies, ticket tracking, and decision logging.
2. **ReAct + Reflection Agent (`gpt-6-astra` via Azure AI Foundry Responses API):** Receives a structured equipment request, investigates via MCP tools using an explicit `Thought → Action → Observation` loop, drafts a decision (`APPROVED`, `DENIED`, or `ESCALATED`), verifies its draft in a **Reflection** step, records final side-effects, and prints per-request **cost & latency telemetry**.

---

## 2. Request Input & Validation Contract

Requests enter the agent as structured input:
- `employee_id` *(string, mandatory)* — must be non-empty. Missing or blank `employee_id` is rejected immediately as invalid input before running the agent.
- `request_text` *(string, mandatory)* — natural-language equipment request (e.g., *"My Dell laptop is 4 years old and slow, can I get a replacement?"*).

---

## 3. Data Model (SQLite, Seeded Deterministically)

No `.db` / `.sqlite*` file is committed to Git; the database is initialized/seeded cleanly from code/seed data.

### 3.1 `employees`
- `employee_id` (PK, e.g., `E101`)
- `name`
- `role` (e.g., `standard_employee`, `engineer`, `manager`, `contractor`, `it_ops_lead`, `hr_lead`, `infosec_lead`)
- `department` (FK → `departments.department`)
- `employment_type` (`fte`, `contractor`, `intern`)
- `work_mode` (`wfh`, `hybrid`, `onsite`)
- `status` (`active`, `probation`, `notice_period`, `offboarding`)
- `hire_date` (`YYYY-MM-DD`)
- `manager_id` (self-FK → `employees.employee_id`, nullable for top-level leads)

*Note:* Standard functional reviewers (e.g., IT-Ops Lead, HR Accommodations Lead, InfoSec Lead) are stored as real rows in `employees` so `tickets.reviewer_employee_id` always points to an actual employee.

### 3.2 `departments`
- `department` (PK)
- `annual_budget_usd` (integer/float)

*Annual Spend Calculation:* Not stored as a mutable counter. Calculated dynamically on every request by summing `resources.unit_cost` across all rows in `employee_resource_dispatches` for employees in that department whose `dispatched_at` falls within the current calendar year (regardless of subsequent `condition_status`, because the department already spent that money).

### 3.3 `resources` (Lightweight Catalog / Ontology)
- `resource_id` (PK, e.g., `RES-MON-27`)
- `name` (exact model name, e.g., `Dell 27" 4K Monitor`)
- `category` (`laptop`, `monitor`, `dock`, `headset`, `keyboard_mouse`, `ergonomic_chair`)
- `tier` (`standard`, `performance`, `specialty`)
- `unit_cost` (float, USD)
- `requires_category` (nullable, e.g., `dock` requires an active `laptop`)
- `aliases` (searchable/comma-separated terms, e.g., `dell 27, ultrasharp, 4k monitor`)

*Explicitly excluded:* No security-restricted hardware flags (`is_restricted`) and no physical warehouse `stock_count`.

### 3.4 `employee_resource_dispatches`
- `dispatch_id` (PK)
- `employee_id` (FK → `employees.employee_id`)
- `resource_id` (FK → `resources.resource_id`)
- `serial_id` (unique asset serial, e.g., `SN-DEL-88412`)
- `dispatched_at` (`YYYY-MM-DD`)
- `returned_at` (`YYYY-MM-DD`, nullable)
- `condition_status` (`active`, `returned`, `damaged`, `lost`, `stolen`)

### 3.5 `policies` (Hybrid Store)
1. **`structured_policies` table** — deterministic eligibility constraints:
   - `policy_id`, `role`, `employment_type`, `work_mode`, `category`, `allowed` (boolean), `max_active_qty`, `refresh_months`, `max_auto_cost_usd`, `min_tenure_months`
2. **`policy_clauses` table** — natural-language rules for exceptions, lifecycle gates, and reviewer routing:
   - `clause_id`, `topic`, `rule_text`, `default_reviewer_role`
   - Covers: `notice_period`/`offboarding` freezes, `probation` tier limits, damaged/defective overrides, lost/stolen InfoSec checks, ergonomic/medical accommodations, duplicate/open-ticket handling, conflicting user vs. record facts, annual department budget overrun escalation (`manager_id`), and the **Standard Reviewer Directory** (employee IDs for IT-Ops, HR Accommodations, InfoSec).

### 3.6 `tickets` (Human-Review & Active/Historical Tickets Only)
- `ticket_id` (PK)
- `employee_id` (FK → `employees.employee_id`)
- `request_text`
- `category` (nullable if request is vague/unclassified)
- `resource_id` (nullable FK → `resources.resource_id`)
- `status` (`open`, `escalated`, `closed`)
- `flag_for_human_review` (boolean)
- `reviewer_employee_id` (FK → `employees.employee_id`)
- `reason`
- `created_at`

### 3.7 `logs` (Final `APPROVED` / `DENIED` Audit Trail Only)
- `log_id` (PK)
- `employee_id` (text; no strict FK so nonexistent IDs like `E999` can be logged as `DENIED`)
- `request_text`
- `decision` (`APPROVED` | `DENIED`)
- `reason`
- `created_at`

*Note:* Does **not** store `ESCALATED` intermediate stages, and does **not** store latency/token/cost telemetry.

---

## 4. Decision Rules & Side Effects

| Outcome | Trigger Conditions | Side Effects |
| :--- | :--- | :--- |
| **APPROVED** | Valid employee in good standing (`active`, or `probation` requesting allowed standard item); eligible per `structured_policies` (role, employment type, work mode, max qty, refresh window, dependencies); cost ≤ `max_auto_cost_usd`; no duplicate open/escalated ticket for same category; remaining annual department budget ≥ `unit_cost`; no conflicting facts or special exceptions. | After Reflection confirms: write `APPROVED` row to `logs` **and** automatically insert a new row into `employee_resource_dispatches` (assumed dispatched). |
| **DENIED** | Nonexistent `employee_id` (e.g., `E999`); contractor requesting company-funded hardware; employee in `notice_period` or `offboarding`; `onsite` employee requesting home-office monitor/dock; missing required dependency; or unambiguous routine request inside cooldown/max-qty with no damage/exception claimed. | After Reflection confirms: write `DENIED` row to `logs`. Do not create a ticket or dispatch. |
| **ESCALATED** | Ambiguous/vague item; employee claims hardware failure/damage or unverified diagnostics (e.g., battery health); lost/stolen replacement (InfoSec); ergonomic/medical accommodation; specialty/high-cost item > `max_auto_cost_usd` (manager approval); reasonable & eligible request when department lacks remaining annual budget (manager approval); existing open/escalated ticket conflict; or facts in request contradict DB records. | Agent calls `flag_for_human_review`, selecting the appropriate `reviewer_employee_id` (`employee.manager_id` or standard IT-Ops / HR / InfoSec reviewer ID), creating a row in `tickets` (`flag_for_human_review = True`, `status = 'escalated'`). Not logged to `logs`. |

---

## 5. MCP Tool Surface

The server exposes the **4 mandatory tools** from the assignment plus focused helper tools so every workflow step stays clean and SRP-compliant:

1. `get_employee_info(employee_id)` *(required)* — returns employee profile (`role`, `department`, `employment_type`, `work_mode`, `status`, `hire_date`, `manager_id`), full `dispatch_history` (exact resource name, category, `serial_id`, dates, condition), and active/recent `tickets`. Returns structured `not_found` if nonexistent.
2. `get_policy_limits(role)` *(required)* — returns `structured_policies` for that role plus `policy_clauses` (natural-language exception/lifecycle rules and the standard reviewer directory).
3. `check_request_eligibility(employee_id, item)` *(required)* — resolves `item` via `resources` ontology, evaluates structured rules, active dispatches/cooldowns, dependencies (`requires_category`), open tickets, and remaining annual department budget (computed dynamically from `employee_resource_dispatches`). Returns structured eligibility status, exact dates/numbers, budget status, and applicable NL exception clauses.
4. `flag_for_human_review(employee_id, request, reason, reviewer_employee_id)` *(required, side-effecting)* — validates `employee_id` and `reviewer_employee_id` against `employees`, inserts an escalated ticket in `tickets`, and returns `{ticket_id, status: "escalated", reviewer_employee_id, ...}`.
5. `lookup_resource(query)` *(helper)* — resolves product names/aliases (e.g., `"Dell 27\" monitor"`) into `resource_id`, `name`, `category`, `tier`, `unit_cost`, and `requires_category`.
6. `finalize_request_decision(employee_id, request_text, decision, reason, resource_id=None)` *(helper, side-effecting, post-reflection)* — called after Reflection confirms an `APPROVED` or `DENIED` outcome: writes to `logs`, and if `APPROVED` (with `resource_id`), automatically inserts a dispatch row into `employee_resource_dispatches`.

---

## 6. Agent Behavior, Reflection & Telemetry

1. **LLM Integration:** Azure AI Foundry OpenAI SDK (`OpenAI(base_url=..., api_key=...)` calling `client.responses.create(model="gpt-6-astra", ...)`). Endpoint, deployment, API key, and token cost rates are configured via `.env` / environment variables.
2. **ReAct Trace:** Explicit `Thought → Action → Observation` loop over MCP tools.
3. **Reflection Step:** Before finalizing any drafted response, the agent performs an explicit self-check verifying that its decision and claims match the observed tool data and don't over-promise unconfirmed outcomes.
4. **Per-Request Telemetry (Approved Enhancement):** Every agent run prints latency (`ms`), LLM call count, token counts (input/output/total), and estimated USD cost computed from `.env` rates (`AZURE_OPENAI_INPUT_COST_PER_1M`, `AZURE_OPENAI_OUTPUT_COST_PER_1M`).

---

## 7. Demo & Evaluation Scenarios (Exceeding Rubric Minimum of 4)

All core paths must be exercised end-to-end:
1. **Clear Approval:** Eligible FTE (e.g., `wfh` engineer outside cooldown, within annual department budget, cost ≤ threshold) → `APPROVED`, logged in `logs`, auto-dispatched in `employee_resource_dispatches`.
2. **Clear Denial (Policy/Status/Unknown):** e.g., `contractor`, `offboarding` employee, `onsite` employee requesting WFH monitor, routine request inside cooldown, and nonexistent `E999` → `DENIED`, logged in `logs`.
3. **Escalation — Damaged / Unverified Hardware Claim or Conflicting Facts:** Routed to IT-Ops Lead (`reviewer_employee_id`) via `flag_for_human_review`.
4. **Escalation — Annual Department Budget Exhausted (or High-Cost Specialty Item):** Reasonable request routed to `employee.manager_id` via `flag_for_human_review`.
5. **Escalation — Ergonomic/Medical or Lost/Stolen Device:** Routed to HR Accommodations Lead or InfoSec Lead via `flag_for_human_review`.
