import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _list_and_call_ping() -> tuple[list[str], str]:
    """Connect to the minimal smoke MCP server and call the ping tool."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "it_equipment_handler.smoke_server"],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            call_result = await session.call_tool("ping", arguments={"message": "hello"})
            reply_text = call_result.content[0].text
            return tool_names, reply_text


def test_smoke_server_registers_single_ping_tool() -> None:
    """Verify the minimal MCP server exposes only the ping tool and responds."""
    tool_names, reply_text = asyncio.run(_list_and_call_ping())
    assert tool_names == ["ping"]
    assert reply_text == "pong: hello"


def test_smoke_client_runs_end_to_end() -> None:
    """Verify the smoke client helper connects to the server and returns ping output."""
    from it_equipment_handler.smoke_client import run_smoke_check

    result = asyncio.run(run_smoke_check(message="hello"))
    assert result["tools"] == ["ping"]
    assert result["reply"] == "pong: hello"
