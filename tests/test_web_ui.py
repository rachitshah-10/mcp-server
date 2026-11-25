import asyncio
import json
from pathlib import Path
from typing import Any


async def _asgi_request(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Send a minimal HTTP request directly to a Starlette ASGI app without external test clients."""
    raw_body = json.dumps(body).encode("utf-8") if body is not None else b""
    headers = [(b"content-type", b"application/json")] if body is not None else []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("testserver", 80),
    }
    sent_body = False
    status_code = 500
    resp_headers: dict[str, str] = {}
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        """Deliver request body bytes once to the ASGI app."""
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        """Capture response status, headers, and body chunks."""
        nonlocal status_code, resp_headers
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
            resp_headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status_code, resp_headers, b"".join(chunks)


def _req(app: Any, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Execute a synchronous ASGI request and decode JSON when applicable."""
    status, headers, raw = asyncio.run(_asgi_request(app, method, path, body))
    if "application/json" in headers.get("content-type", ""):
        return status, json.loads(raw.decode("utf-8"))
    return status, raw.decode("utf-8")


def test_web_ui_serves_html_and_full_state(tmp_path: Path) -> None:
    """Verify GET / serves the UI shell and GET /api/state returns all database entities."""
    from it_equipment_handler.web_api import create_app

    app = create_app(db_path=tmp_path / "ui.db")
    status, html = _req(app, "GET", "/")
    assert status == 200
    assert "<!DOCTYPE html>" in html

    status, state = _req(app, "GET", "/api/state")
    assert status == 200
    assert len(state["employees"]) >= 10
    assert len(state["departments"]) >= 5
    assert len(state["structured_policies"]) >= 10
    assert len(state["policy_clauses"]) >= 8
    assert len(state["resources"]) >= 6


def test_web_ui_updates_policies_clauses_budgets_resources_and_employees(tmp_path: Path) -> None:
    """Verify admin PUT/POST/reset endpoints mutate SQLite state and persist cleanly."""
    from it_equipment_handler.web_api import create_app

    app = create_app(db_path=tmp_path / "ui_mutate.db")

    status, pol = _req(
        app,
        "PUT",
        "/api/policies/POL-ENG-LAP",
        {
            "allowed": True,
            "max_active_qty": 2,
            "refresh_months": 18,
            "max_auto_cost_usd": 2500.0,
            "min_tenure_months": 3,
        },
    )
    assert status == 200
    assert pol["refresh_months"] == 18
    assert pol["max_auto_cost_usd"] == 2500.0

    status, clause = _req(
        app,
        "PUT",
        "/api/clauses/CLS-BUDGET",
        {"rule_text": "Escalate any budget overrun to the department VP or manager_id."},
    )
    assert status == 200
    assert "department VP" in clause["rule_text"]

    status, created = _req(
        app,
        "POST",
        "/api/clauses",
        {
            "topic": "travel_kit",
            "rule_text": "Frequent travelers qualify for a portable monitor with manager approval.",
            "default_reviewer_role": "manager",
        },
    )
    assert status == 201
    assert created["clause_id"].startswith("CLS-")

    status, dept = _req(app, "PUT", "/api/departments/Operations/budget", {"annual_budget_usd": 5000.0})
    assert status == 200
    assert dept["annual_budget_usd"] == 5000.0
    assert dept["remaining_budget_usd"] > 3000.0

    status, res = _req(app, "PUT", "/api/resources/RES-MON-27", {"unit_cost": 499.0})
    assert status == 200
    assert res["unit_cost"] == 499.0

    status, emp = _req(app, "PUT", "/api/employees/E104", {"status": "active", "work_mode": "wfh"})
    assert status == 200
    assert emp["work_mode"] == "wfh"

    status, reset_res = _req(app, "POST", "/api/db/reset")
    assert status == 200
    assert reset_res["reset"] is True

    _, state_after = _req(app, "GET", "/api/state")
    ops = next(d for d in state_after["departments"] if d["department"] == "Operations")
    assert ops["annual_budget_usd"] == 2000.0


def test_web_ui_runs_agent_request_endpoint(tmp_path: Path) -> None:
    """Verify POST /api/requests/run executes the ReAct agent and returns trace, reflection, and telemetry."""
    from it_equipment_handler.llm_client import LLMResponse
    from it_equipment_handler.web_api import create_app

    scripted = [
        json.dumps({
            "thought": "Check E999 in directory.",
            "action": "get_employee_info",
            "action_input": {"employee_id": "E999"},
        }),
        json.dumps({
            "thought": "Employee not found.",
            "decision": "DENIED",
            "resource_id": None,
            "reason": "Employee E999 not found.",
            "draft_response": "Denied: employee E999 not found.",
        }),
        json.dumps({
            "status": "CONFIRMED",
            "critique": "Matches get_employee_info observation.",
            "decision": "DENIED",
            "resource_id": None,
            "reason": "Employee E999 not found.",
            "final_response": "Denied: employee E999 not found.",
        }),
    ]

    class StubLLM:
        """Scripted LLM for testing POST /api/requests/run without live API calls."""

        def generate(self, prompt: str) -> LLMResponse:
            """Return the next scripted JSON step."""
            return LLMResponse(text=scripted.pop(0), input_tokens=100, output_tokens=20)

    app = create_app(db_path=tmp_path / "ui_agent.db", llm_client_factory=lambda: StubLLM())
    status, payload = _req(
        app,
        "POST",
        "/api/requests/run",
        {"employee_id": "E999", "request_text": "Can I get a laptop?"},
    )
    assert status == 200
    assert payload["decision"] == "DENIED"
    assert len(payload["trace"]) == 1
    assert payload["reflection"]["status"] == "CONFIRMED"
    assert payload["telemetry"]["llm_calls"] == 3
