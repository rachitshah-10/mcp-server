import json
import os
import sqlite3
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
from it_equipment_handler.seed import seed_database
from it_equipment_handler.service import EquipmentService

DEFAULT_DB_PATH = ".local_equipment.db"

mcp = MCPServer("it-equipment-handler")


def _get_service() -> tuple[EquipmentService, sqlite3.Connection]:
    """Open and seed the configured SQLite DB and return a service instance."""
    db_path = Path(os.environ.get("IT_EQUIPMENT_DB_PATH", DEFAULT_DB_PATH))
    conn = create_connection(db_path)
    init_schema(conn)
    seed_database(conn)
    return EquipmentService(SqliteEquipmentRepository(conn)), conn


@mcp.tool()
def get_employee_info(employee_id: str) -> str:
    """Return an employee's role, tenure, equipment history, tickets, and department budget."""
    service, conn = _get_service()
    try:
        return json.dumps(service.get_employee_info(employee_id), indent=2)
    finally:
        conn.close()


@mcp.tool()
def get_policy_limits(role: str) -> str:
    """Return structured policy limits for a role plus natural-language policy clauses."""
    service, conn = _get_service()
    try:
        return json.dumps(service.get_policy_limits(role), indent=2)
    finally:
        conn.close()


@mcp.tool()
def lookup_resource(query: str) -> str:
    """Resolve an equipment name, category, or alias against the resource catalog."""
    service, conn = _get_service()
    try:
        return json.dumps(service.lookup_resource(query), indent=2)
    finally:
        conn.close()


@mcp.tool()
def check_request_eligibility(employee_id: str, item: str) -> str:
    """Check whether an employee's equipment request satisfies policy, dependencies, and budget."""
    service, conn = _get_service()
    try:
        return json.dumps(service.check_request_eligibility(employee_id, item), indent=2)
    finally:
        conn.close()


@mcp.tool()
def flag_for_human_review(
    employee_id: str,
    request: str,
    reason: str,
    reviewer_employee_id: str,
    category: str | None = None,
    resource_id: str | None = None,
) -> str:
    """Escalate an equipment request to a human reviewer by creating a review ticket."""
    service, conn = _get_service()
    try:
        result = service.flag_for_human_review(
            employee_id=employee_id,
            request=request,
            reason=reason,
            reviewer_employee_id=reviewer_employee_id,
            category=category,
            resource_id=resource_id,
        )
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def finalize_request_decision(
    employee_id: str,
    request_text: str,
    decision: str,
    reason: str,
    resource_id: str | None = None,
) -> str:
    """Record a finalized APPROVED or DENIED decision and auto-dispatch approved items."""
    service, conn = _get_service()
    try:
        result = service.finalize_request_decision(
            employee_id=employee_id,
            request_text=request_text,
            decision=decision,
            reason=reason,
            resource_id=resource_id,
        )
        return json.dumps(result, indent=2)
    finally:
        conn.close()


def main() -> None:
    """Run the IT equipment MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
