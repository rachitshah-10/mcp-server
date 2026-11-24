from mcp.server.mcpserver import MCPServer

mcp = MCPServer("smoke-server")


@mcp.tool()
def ping(message: str = "hello") -> str:
    """Return a pong response to verify minimal MCP tool connectivity."""
    return f"pong: {message}"


def main() -> None:
    """Start the minimal MCP smoke server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
