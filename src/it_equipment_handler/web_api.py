from datetime import date
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from it_equipment_handler.agent import EquipmentRequestAgent
from it_equipment_handler.config import load_settings
from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
from it_equipment_handler.llm_client import AzureFoundryLLMClient, LLMClient
from it_equipment_handler.seed import seed_database

STATIC_DIR = Path(__file__).parent / "static"


def _open_seeded_conn(db_path: Path):
    """Open SQLite connection, ensure schema exists, and seed if empty."""
    conn = create_connection(db_path)
    init_schema(conn)
    seed_database(conn)
    return conn


def _read_full_state(db_path: Path) -> dict[str, Any]:
    """Read a complete live snapshot of all tables and dynamic annual department budgets."""
    conn = _open_seeded_conn(db_path)
    try:
        repo = SqliteEquipmentRepository(conn)
        year = date.today().year

        employees = [
            dict(r)
            for r in conn.execute("SELECT * FROM employees ORDER BY employee_id").fetchall()
        ]
        dept_rows = conn.execute("SELECT department FROM departments ORDER BY department").fetchall()
        departments = [repo.get_department_annual_budget(r["department"], year) for r in dept_rows]
        resources = [
            dict(r)
            for r in conn.execute("SELECT * FROM resources ORDER BY category, unit_cost").fetchall()
        ]
        policies = []
        for r in conn.execute("SELECT * FROM structured_policies ORDER BY role, category").fetchall():
            item = dict(r)
            item["allowed"] = bool(item["allowed"])
            policies.append(item)
        clauses = repo.get_policy_clauses()
        tickets = []
        for r in conn.execute("SELECT * FROM tickets ORDER BY created_at DESC, ticket_id DESC").fetchall():
            item = dict(r)
            item["flag_for_human_review"] = bool(item["flag_for_human_review"])
            tickets.append(item)
        dispatches = [
            dict(r)
            for r in conn.execute(
                """
                SELECT d.*, r.name AS resource_name, r.category, r.unit_cost
                FROM employee_resource_dispatches d
                JOIN resources r ON r.resource_id = d.resource_id
                ORDER BY d.dispatched_at DESC, d.dispatch_id DESC
                """
            ).fetchall()
        ]
        logs = [
            dict(r)
            for r in conn.execute("SELECT * FROM logs ORDER BY created_at DESC, log_id DESC").fetchall()
        ]
        return {
            "year": year,
            "employees": employees,
            "departments": departments,
            "resources": resources,
            "structured_policies": policies,
            "policy_clauses": clauses,
            "tickets": tickets,
            "dispatches": dispatches,
            "logs": logs,
        }
    finally:
        conn.close()


def create_app(
    db_path: str | Path | None = None,
    llm_client_factory: Callable[[], LLMClient] | None = None,
) -> Starlette:
    """Create the Starlette ASGI application serving the UI and live admin/agent APIs."""
    settings = load_settings()
    resolved_db = Path(db_path) if db_path is not None else settings.db_path

    def _build_llm() -> LLMClient:
        """Build the configured Azure Foundry LLM client unless overridden by a test factory."""
        if llm_client_factory is not None:
            return llm_client_factory()
        fresh = load_settings()
        return AzureFoundryLLMClient(
            endpoint=fresh.azure_openai_endpoint,
            api_key=fresh.azure_openai_api_key,
            deployment_name=fresh.azure_openai_deployment,
        )

    async def index(_: Request) -> HTMLResponse:
        """Serve the main single-page web UI."""
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    async def get_state(_: Request) -> JSONResponse:
        """Return full live database state for the UI."""
        return JSONResponse(_read_full_state(resolved_db))

    async def run_request(request: Request) -> JSONResponse:
        """Execute the ReAct + Reflection agent for a submitted equipment request."""
        try:
            body = await request.json()
            emp_id = str(body.get("employee_id", ""))
            req_text = str(body.get("request_text", ""))
            fresh = load_settings()
            agent = EquipmentRequestAgent(
                llm_client=_build_llm(),
                db_path=resolved_db,
                input_cost_per_1m=fresh.input_cost_per_1m,
                output_cost_per_1m=fresh.output_cost_per_1m,
            )
            result = await agent.handle_request(emp_id, req_text)
            return JSONResponse(
                {
                    "employee_id": result.employee_id,
                    "request_text": result.request_text,
                    "decision": result.decision,
                    "resource_id": result.resource_id,
                    "reason": result.reason,
                    "final_response": result.final_response,
                    "trace": [
                        {
                            "thought": step.thought,
                            "action": step.action,
                            "action_input": step.action_input,
                            "observation": step.observation,
                        }
                        for step in result.trace
                    ],
                    "reflection": result.reflection,
                    "telemetry": result.telemetry,
                }
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def update_policy(request: Request) -> JSONResponse:
        """Update a structured policy row's limits in SQLite."""
        policy_id = request.path_params["policy_id"]
        body = await request.json()
        conn = _open_seeded_conn(resolved_db)
        try:
            row = conn.execute(
                "SELECT * FROM structured_policies WHERE policy_id = ?", (policy_id,)
            ).fetchone()
            if row is None:
                return JSONResponse({"error": f"Policy '{policy_id}' not found."}, status_code=404)
            allowed = 1 if body.get("allowed", bool(row["allowed"])) else 0
            max_active_qty = int(body.get("max_active_qty", row["max_active_qty"]))
            refresh_months = int(body.get("refresh_months", row["refresh_months"]))
            max_auto_cost_usd = float(body.get("max_auto_cost_usd", row["max_auto_cost_usd"]))
            min_tenure_months = int(body.get("min_tenure_months", row["min_tenure_months"]))

            conn.execute(
                """
                UPDATE structured_policies
                SET allowed = ?, max_active_qty = ?, refresh_months = ?,
                    max_auto_cost_usd = ?, min_tenure_months = ?
                WHERE policy_id = ?
                """,
                (allowed, max_active_qty, refresh_months, max_auto_cost_usd, min_tenure_months, policy_id),
            )
            conn.commit()
            updated = dict(
                conn.execute("SELECT * FROM structured_policies WHERE policy_id = ?", (policy_id,)).fetchone()
            )
            updated["allowed"] = bool(updated["allowed"])
            return JSONResponse(updated)
        finally:
            conn.close()

    async def update_clause(request: Request) -> JSONResponse:
        """Update an existing natural-language policy clause."""
        clause_id = request.path_params["clause_id"]
        body = await request.json()
        conn = _open_seeded_conn(resolved_db)
        try:
            row = conn.execute(
                "SELECT * FROM policy_clauses WHERE clause_id = ?", (clause_id,)
            ).fetchone()
            if row is None:
                return JSONResponse({"error": f"Clause '{clause_id}' not found."}, status_code=404)
            rule_text = str(body.get("rule_text", row["rule_text"])).strip()
            if not rule_text:
                return JSONResponse({"error": "rule_text cannot be empty."}, status_code=400)
            reviewer_role = body.get("default_reviewer_role", row["default_reviewer_role"])
            conn.execute(
                "UPDATE policy_clauses SET rule_text = ?, default_reviewer_role = ? WHERE clause_id = ?",
                (rule_text, reviewer_role, clause_id),
            )
            conn.commit()
            updated = dict(
                conn.execute("SELECT * FROM policy_clauses WHERE clause_id = ?", (clause_id,)).fetchone()
            )
            return JSONResponse(updated)
        finally:
            conn.close()

    async def create_clause(request: Request) -> JSONResponse:
        """Create a new natural-language policy clause."""
        body = await request.json()
        topic = str(body.get("topic", "")).strip().lower().replace(" ", "_")
        rule_text = str(body.get("rule_text", "")).strip()
        reviewer_role = body.get("default_reviewer_role") or None
        if not topic or not rule_text:
            return JSONResponse({"error": "topic and rule_text are required."}, status_code=400)
        clause_id = f"CLS-{uuid4().hex[:6].upper()}"
        conn = _open_seeded_conn(resolved_db)
        try:
            conn.execute(
                "INSERT INTO policy_clauses (clause_id, topic, rule_text, default_reviewer_role) VALUES (?, ?, ?, ?)",
                (clause_id, topic, rule_text, reviewer_role),
            )
            conn.commit()
            return JSONResponse(
                {
                    "clause_id": clause_id,
                    "topic": topic,
                    "rule_text": rule_text,
                    "default_reviewer_role": reviewer_role,
                },
                status_code=201,
            )
        finally:
            conn.close()

    async def update_budget(request: Request) -> JSONResponse:
        """Update a department's annual_budget_usd and return refreshed dynamic spend."""
        department = request.path_params["department"]
        body = await request.json()
        new_budget = float(body["annual_budget_usd"])
        if new_budget < 0:
            return JSONResponse({"error": "annual_budget_usd must be non-negative."}, status_code=400)
        conn = _open_seeded_conn(resolved_db)
        try:
            cur = conn.execute(
                "UPDATE departments SET annual_budget_usd = ? WHERE department = ?",
                (new_budget, department),
            )
            if cur.rowcount == 0:
                return JSONResponse({"error": f"Department '{department}' not found."}, status_code=404)
            conn.commit()
            repo = SqliteEquipmentRepository(conn)
            return JSONResponse(repo.get_department_annual_budget(department, date.today().year))
        finally:
            conn.close()

    async def update_resource(request: Request) -> JSONResponse:
        """Update a catalog resource's unit_cost."""
        resource_id = request.path_params["resource_id"]
        body = await request.json()
        new_cost = float(body["unit_cost"])
        if new_cost < 0:
            return JSONResponse({"error": "unit_cost must be non-negative."}, status_code=400)
        conn = _open_seeded_conn(resolved_db)
        try:
            cur = conn.execute(
                "UPDATE resources SET unit_cost = ? WHERE resource_id = ?",
                (new_cost, resource_id),
            )
            if cur.rowcount == 0:
                return JSONResponse({"error": f"Resource '{resource_id}' not found."}, status_code=404)
            conn.commit()
            updated = dict(
                conn.execute("SELECT * FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            )
            return JSONResponse(updated)
        finally:
            conn.close()

    async def update_employee(request: Request) -> JSONResponse:
        """Update an employee's status or work_mode for live scenario testing."""
        employee_id = request.path_params["employee_id"]
        body = await request.json()
        conn = _open_seeded_conn(resolved_db)
        try:
            row = conn.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
            if row is None:
                return JSONResponse({"error": f"Employee '{employee_id}' not found."}, status_code=404)
            new_status = str(body.get("status", row["status"])).strip()
            new_mode = str(body.get("work_mode", row["work_mode"])).strip()
            conn.execute(
                "UPDATE employees SET status = ?, work_mode = ? WHERE employee_id = ?",
                (new_status, new_mode, employee_id),
            )
            conn.commit()
            updated = dict(
                conn.execute("SELECT * FROM employees WHERE employee_id = ?", (employee_id,)).fetchone()
            )
            return JSONResponse(updated)
        finally:
            conn.close()

    async def reset_db(_: Request) -> JSONResponse:
        """Delete and re-seed the SQLite database back to clean synthetic defaults."""
        if resolved_db.exists():
            resolved_db.unlink()
        conn = _open_seeded_conn(resolved_db)
        conn.close()
        return JSONResponse({"reset": True})

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api/state", get_state, methods=["GET"]),
        Route("/api/requests/run", run_request, methods=["POST"]),
        Route("/api/policies/{policy_id}", update_policy, methods=["PUT"]),
        Route("/api/clauses", create_clause, methods=["POST"]),
        Route("/api/clauses/{clause_id}", update_clause, methods=["PUT"]),
        Route("/api/departments/{department}/budget", update_budget, methods=["PUT"]),
        Route("/api/resources/{resource_id}", update_resource, methods=["PUT"]),
        Route("/api/employees/{employee_id}", update_employee, methods=["PUT"]),
        Route("/api/db/reset", reset_db, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]
    return Starlette(routes=routes)


def main() -> None:
    """Launch the IT Equipment Control Plane web UI with Uvicorn."""
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
