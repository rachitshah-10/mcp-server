import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call_employee_info_over_mcp(employee_id: str = "E101") -> tuple[list[str], dict]:
    """Connect to the MCP server over stdio, list tools, and call get_employee_info."""
    env = {}
    if "IT_EQUIPMENT_DB_PATH" in os.environ:
        env["IT_EQUIPMENT_DB_PATH"] = os.environ["IT_EQUIPMENT_DB_PATH"]

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "it_equipment_handler.mcp_server"],
        env=env or None,
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


def main() -> None:
    """Verify end-to-end 1-tool MCP connectivity for Deliverable Step 3."""
    tool_names, payload = asyncio.run(call_employee_info_over_mcp("E101"))
    print(f"Registered MCP tools: {tool_names}")
    print("get_employee_info('E101') response:")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
