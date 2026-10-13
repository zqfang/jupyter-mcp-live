"""Images from tools called inside Code Mode scripts must reach the model.

Code Mode hands each nested tool result to the sandbox as structured content
or text, so image blocks would otherwise be dropped (or stringified to
base64). The forwarder middleware moves them onto the execute response.
"""

import base64

import nbformat
import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import ImageContent, TextContent
from nbformat.v4 import new_code_cell, new_notebook, new_output

from jupyter_mcp_executor.code_mode_images import CodeModeImageForwarder
from jupyter_mcp_executor.execution_tools import view_cell_image
from jupyter_mcp_executor.mcp_server import MCPServer
from jupyter_mcp_executor.notebook_config import IMAGE_CONFIG

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG_BYTES).decode()


def _images(result) -> list[ImageContent]:
    return [c for c in result.content if isinstance(c, ImageContent)]


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


async def plot_tool() -> ToolResult:
    """Return a summary dict plus one image block."""
    return ToolResult(
        content=[
            TextContent(type="text", text="plot"),
            ImageContent(type="image", data=PNG_B64, mimeType="image/png"),
        ],
        structured_content={"images": 1},
    )


async def bare_image_tool() -> Image:
    """Return a bare image (no structured content)."""
    return Image(data=PNG_BYTES, format="png")


async def many_images_tool() -> ToolResult:
    """Return more images than one execute response may carry."""
    count = IMAGE_CONFIG.max_images_per_response + 2
    return ToolResult(
        content=[
            ImageContent(type="image", data=PNG_B64, mimeType="image/png")
            for _ in range(count)
        ],
        structured_content={"images": count},
    )


def _server(*tools) -> MCPServer:
    server = MCPServer(name="Images")
    for tool in tools:
        server.register_tool(tool)
    return server


class TestCodeModeImageForwarder:
    @pytest.mark.asyncio
    async def test_image_from_nested_call_reaches_execute_response(self):
        server = _server(plot_tool)

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": "return await call_tool('plot_tool', {})"}
            )

        assert [i.data for i in _images(result)] == [PNG_B64]
        # The script saw the structured summary, never the base64 payload.
        assert '"images"' in _text(result)
        assert PNG_B64 not in _text(result)

    @pytest.mark.asyncio
    async def test_images_from_several_calls_are_all_forwarded(self):
        server = _server(plot_tool)
        code = (
            "await call_tool('plot_tool', {})\n"
            "await call_tool('plot_tool', {})\n"
            "return 'done'"
        )

        async with Client(server.mcp) as client:
            result = await client.call_tool("execute", {"code": code})

        assert len(_images(result)) == 2
        assert "done" in _text(result)

    @pytest.mark.asyncio
    async def test_bare_image_does_not_leak_base64_into_sandbox(self):
        server = _server(bare_image_tool)

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": "return await call_tool('bare_image_tool', {})"}
            )

        assert len(_images(result)) == 1
        assert PNG_B64 not in _text(result)
        assert "attached" in _text(result)

    @pytest.mark.asyncio
    async def test_response_image_count_is_capped(self):
        server = _server(many_images_tool)

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": "return await call_tool('many_images_tool', {})"}
            )

        assert len(_images(result)) == IMAGE_CONFIG.max_images_per_response
        assert "2 more image(s)" in _text(result)

    @pytest.mark.asyncio
    async def test_execute_without_images_is_unchanged(self):
        async def ping() -> str:
            """Return a string."""
            return "pong"

        server = _server(ping)

        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": "return await call_tool('ping', {})"}
            )

        assert _images(result) == []
        assert "pong" in _text(result)

    @pytest.mark.asyncio
    async def test_direct_call_without_code_mode_keeps_images(self):
        mcp = FastMCP("plain", middleware=[CodeModeImageForwarder()])
        mcp.tool(plot_tool)

        async with Client(mcp) as client:
            result = await client.call_tool("plot_tool", {})

        assert [i.data for i in _images(result)] == [PNG_B64]

    def test_server_installs_forwarder(self):
        server = MCPServer()

        assert any(isinstance(m, CodeModeImageForwarder) for m in server.mcp.middleware)


@pytest.mark.asyncio
async def test_view_cell_image_end_to_end_through_execute(tmp_path):
    """The real tool, called from a Code Mode script, shows the model the plot."""
    cell = new_code_cell("plt.plot([1, 2])", execution_count=1)
    cell.outputs = [new_output("display_data", data={"image/png": PNG_B64})]
    path = tmp_path / "plot.ipynb"
    nbformat.write(new_notebook(cells=[cell]), str(path))
    server = _server(view_cell_image)
    code = (
        "r = await call_tool('view_cell_image', "
        f"{{'notebook_path': {str(path)!r}, 'position_index': 0}})\n"
        "return r['images']"
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool("execute", {"code": code})

    assert [i.data for i in _images(result)] == [PNG_B64]
    assert "image/png" in _text(result)
    assert PNG_B64 not in _text(result)
