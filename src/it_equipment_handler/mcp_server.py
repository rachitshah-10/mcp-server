import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from it_equipment_handler.db import SqliteEquipmentRepository, create_connection, init_schema
from it_equipment_handler.seed import seed_database
from it_equipment_handler.service import EquipmentService

DEFAULT_DB_PATH = ".local_equipment.db"

mcp = MCPServer("it-equipment-handler")


def _get_service() -> tuple[EquipmentService, object]:
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
        result = service.get_employee_info(employee_id)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


def main() -> None:
    """Run the IT equipment MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
