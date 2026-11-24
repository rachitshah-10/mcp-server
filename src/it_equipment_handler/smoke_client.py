import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run_smoke_check(message: str = "hello") -> dict[str, object]:
    """Connect to smoke_server over stdio, list tools, and call ping."""
    # Launch the server as a child process using the active Python interpreter.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "it_equipment_handler.smoke_server"],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]

            call_result = await session.call_tool("ping", arguments={"message": message})
            reply_text = call_result.content[0].text
            return {"tools": tool_names, "reply": reply_text}


def main() -> None:
    """Run the minimal MCP smoke check and print tool output."""
    result = asyncio.run(run_smoke_check())
    print(f"Registered tools: {result['tools']}")
    print(f"ping('hello') -> {result['reply']}")


if __name__ == "__main__":
    main()
