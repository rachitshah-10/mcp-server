import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _build_service(tmp_path: Path):
    """Create an isolated seeded SQLite database and return EquipmentService."""
    from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
    from it_equipment_handler.seed import seed_database
    from it_equipment_handler.service import EquipmentService

    db_path = tmp_path / "test_equipment.db"
    conn = create_connection(db_path)
    init_schema(conn)
    seed_database(conn)
    repo = SqliteEquipmentRepository(conn)
    return EquipmentService(repo), conn


def test_get_employee_info_returns_profile_dispatches_and_budget(tmp_path: Path) -> None:
    """Verify get_employee_info returns profile, dispatch history, and dynamic budget."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.get_employee_info("E101")
        assert result["found"] is True
        assert result["employee_id"] == "E101"
        assert result["role"] == "engineer"
        assert result["work_mode"] == "wfh"
        assert result["status"] == "active"
        assert result["manager_id"] == "E100"
        assert result["tenure_months"] >= 48
        assert len(result["dispatch_history"]) >= 1
        first_item = result["dispatch_history"][0]
        assert "resource_name" in first_item
        assert "category" in first_item
        assert "serial_id" in first_item
        budget = result["department_budget"]
        assert budget["department"] == "Engineering"
        assert budget["remaining_budget_usd"] == (
            budget["annual_budget_usd"] - budget["annual_spend_usd"]
        )
    finally:
        conn.close()


def test_get_employee_info_includes_open_tickets(tmp_path: Path) -> None:
    """Verify get_employee_info includes existing tickets for duplicate detection."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.get_employee_info("E110")
        assert result["found"] is True
        assert len(result["tickets"]) >= 1
        assert any(t["status"] in ("open", "escalated") for t in result["tickets"])
    finally:
        conn.close()


def test_get_employee_info_calculates_annual_spend_from_current_year_dispatches(tmp_path: Path) -> None:
    """Verify annual department spend sums current-year dispatches regardless of condition."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.get_employee_info("E107")
        assert result["found"] is True
        budget = result["department_budget"]
        assert budget["department"] == "Operations"
        assert budget["annual_spend_usd"] > 0
        assert budget["remaining_budget_usd"] < 600
    finally:
        conn.close()


def test_get_employee_info_unknown_employee_returns_not_found(tmp_path: Path) -> None:
    """Verify nonexistent employee ID returns a clean not_found payload."""
    service, conn = _build_service(tmp_path)
    try:
        result = service.get_employee_info("E999")
        assert result["found"] is False
        assert result["employee_id"] == "E999"
        assert "not found" in result["message"].lower()
    finally:
        conn.close()


def test_get_employee_info_blank_id_raises_value_error(tmp_path: Path) -> None:
    """Verify missing or blank employee_id is rejected as invalid input."""
    service, conn = _build_service(tmp_path)
    try:
        with pytest.raises(ValueError):
            service.get_employee_info("   ")
    finally:
        conn.close()


async def _call_mcp_get_employee_info(db_path: Path, employee_id: str) -> tuple[list[str], dict]:
    """Connect to the MCP server over stdio and invoke get_employee_info."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "it_equipment_handler.mcp_server"],
        env={"IT_EQUIPMENT_DB_PATH": str(db_path)},
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            call_result = await session.call_tool(
                "get_employee_info",
                arguments={"employee_id": employee_id},
            )
            payload = json.loads(call_result.content[0].text)
            return tool_names, payload


def test_mcp_server_exposes_and_runs_get_employee_info(tmp_path: Path) -> None:
    """Verify get_employee_info works end-to-end through the MCP stdio server."""
    db_path = tmp_path / "mcp_employee.db"
    tool_names, payload = asyncio.run(_call_mcp_get_employee_info(db_path, "E101"))
    assert "get_employee_info" in tool_names
    assert payload["found"] is True
    assert payload["employee_id"] == "E101"
    assert len(payload["dispatch_history"]) >= 1
