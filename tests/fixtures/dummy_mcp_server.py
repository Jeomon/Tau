"""Minimal stdio MCP server used by tests/test_mcp_extension.py."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dummy")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back."""
    return text


if __name__ == "__main__":
    mcp.run(transport="stdio")
