import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPToolClient:
    """Async context manager for calling tools on the IT equipment MCP server over stdio."""

    def __init__(self, db_path: str | Path) -> None:
        """Configure the stdio server subprocess with the target SQLite DB path."""
        self._db_path = str(db_path)
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPToolClient":
        """Spawn the MCP server subprocess and initialize the client session."""
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "it_equipment_handler.mcp_server"],
            env={"IT_EQUIPMENT_DB_PATH": self._db_path},
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(server_params))
        self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Close the MCP client session and terminate the stdio server subprocess."""
        await self._stack.aclose()
        self._session = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return tool names, descriptions, and input schemas registered on the server."""
        if self._session is None:
            raise RuntimeError("MCPToolClient must be entered with 'async with' before use.")
        result = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None),
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke an MCP tool by name and return its text observation."""
        if self._session is None:
            raise RuntimeError("MCPToolClient must be entered with 'async with' before use.")
        result = await self._session.call_tool(name, arguments=arguments)
        return "\n".join(getattr(block, "text", str(block)) for block in result.content)
