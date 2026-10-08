"""MCP stdio smoke tests: the real server over stdio, as an agent sees it."""

import sys

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

EXPECTED_TOOLS = {
    "dt_load",
    "dt_tree",
    "dt_node",
    "dt_find",
    "dt_memory_map",
    "dt_interrupt_map",
    "dt_validate",
    "dt_diff",
    "dt_trees",
}


@pytest.mark.anyio
async def test_stdio_handshake_lists_expected_tools():
    params = StdioServerParameters(command=sys.executable, args=["-m", "devicetree_mcp"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init_result = await session.initialize()
        assert init_result.server_info.name == "devicetree"
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == EXPECTED_TOOLS, (
            f"missing: {EXPECTED_TOOLS - names}, unexpected: {names - EXPECTED_TOOLS}"
        )


@pytest.mark.anyio
async def test_instructive_errors_reach_the_agent(tmp_path):
    """MCPServer hides any exception that isn't a ToolError behind a generic
    "Error executing tool X"; our next-step guidance must survive the trip."""
    not_a_dtb = tmp_path / "board.txt"
    not_a_dtb.write_bytes(b"\x00" * 64)
    params = StdioServerParameters(command=sys.executable, args=["-m", "devicetree_mcp"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        result = await session.call_tool("dt_tree", {"name": "nope"})
        assert result.is_error
        assert "call dt_load first" in result.content[0].text

        result = await session.call_tool("dt_load", {"name": "x", "path": str(not_a_dtb)})
        assert result.is_error
        text = result.content[0].text
        # Either the dtc install hint (no dtc) or dtc's own error - never a bare failure.
        assert "dtc" in text, text


@pytest.fixture
def anyio_backend():
    return "asyncio"
