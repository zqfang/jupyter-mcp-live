"""What the tools hand the model: no surprises, no wasted tokens.

- Inserting a cell at a position that does not exist is an error, not a
  silent append (the agent would report the wrong position).
- list_notebook_kernels returns what an agent needs to pick a kernel, not
  each kernelspec's argv and env (tokens, and env may hold values).
- A filtered image says how to see it.
"""

import nbformat
import pytest
from jupyter_ydoc import YNotebook
from nbformat.v4 import new_code_cell, new_notebook

from jupyter_mcp_executor.execution_tools import (
    list_notebook_kernels,
    list_notebook_sessions,
    read_cell,
    view_notebook,
)
from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.server_context import set_server_context
from jupyter_mcp_executor.ydoc_adapter import YDocAdapter
from tests.test_notebook_execution_tools import MockKernelSpecManager, MockServerApp


async def _ydoc_adapter(tmp_path):
    adapter = YDocAdapter(YNotebook(), str(tmp_path / "nb.ipynb"))
    await adapter.add_cell(CellContent(source="x", cell_type="code"))
    return adapter


async def _file_adapter(tmp_path):
    path = tmp_path / "nb.ipynb"
    nbformat.write(new_notebook(cells=[new_code_cell("x")]), path)
    return NbformatAdapter(str(path))


ADAPTERS = pytest.mark.parametrize(
    "make_adapter", [_ydoc_adapter, _file_adapter], ids=["ydoc", "file"]
)


@ADAPTERS
@pytest.mark.parametrize("position", [-1, 2, 99])
@pytest.mark.asyncio
async def test_inserting_at_a_missing_position_is_an_error(
    tmp_path, make_adapter, position
):
    adapter = await make_adapter(tmp_path)

    with pytest.raises(IndexError, match="omit position_index to append"):
        await adapter.add_cell(CellContent(source="y", cell_type="code"), position)

    assert [c.source for c in await adapter.get_all_cells()] == ["x"]


@ADAPTERS
@pytest.mark.asyncio
async def test_inserting_at_the_cell_count_appends(tmp_path, make_adapter):
    adapter = await make_adapter(tmp_path)

    position, _ = await adapter.add_cell(CellContent(source="y", cell_type="code"), 1)

    assert position == 1
    assert [c.source for c in await adapter.get_all_cells()] == ["x", "y"]


class _SpecsWithEnv(MockKernelSpecManager):
    def get_all_specs(self):
        return {
            name: {
                "resource_dir": f"/opt/kernels/{name}",
                "spec": {
                    **entry["spec"],
                    "argv": ["python", "-m", "ipykernel_launcher", "-f", "{f}"],
                    "env": {"API_KEY": "secret"},
                    "interrupt_mode": "signal",
                    "metadata": {"debugger": True},
                },
            }
            for name, entry in super().get_all_specs().items()
        }


@pytest.mark.asyncio
async def test_kernel_list_has_what_an_agent_needs_only(tmp_path):
    app = MockServerApp(tmp_path)
    app.kernel_spec_manager = _SpecsWithEnv()
    app.kernel_manager.default_kernel_name = "python3"
    set_server_context(app)
    try:
        result = await list_notebook_kernels()
    finally:
        set_server_context(None)
        app.close()

    assert result == {
        "default_kernel": "python3",
        "kernelspecs": {
            "python3": {"display_name": "Python 3", "language": "python"},
            "ir": {"display_name": "R", "language": "R"},
        },
    }


def _placeholder_text(mime_types):
    return OutputFilter.create_placeholder(mime_types)["data"]["text/plain"]


def test_filtered_image_points_to_view_cell_image():
    text = _placeholder_text(["image/png", "text/plain"])

    assert "image/png" in text
    assert "view_cell_image" in text


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml"])
def test_filtered_content_the_model_cannot_view_does_not(mime):
    """view_cell_image shows raster images only."""
    text = _placeholder_text([mime])

    assert mime in text
    assert "view_cell_image" not in text


@pytest.mark.asyncio
async def test_cell_id_is_returned_under_the_name_tools_take_it(tmp_path):
    """read_cell / view_notebook return the id as ``cell_id`` (the parameter
    name of read_cell, view_cell_image), so it can be passed straight back;
    ``id`` stays for compatibility."""
    path = tmp_path / "nb.ipynb"
    nbformat.write(new_notebook(cells=[new_code_cell("x = 1")]), path)

    cells = await view_notebook(str(path))
    cell_id = cells[0]["cell_id"]
    by_id = await read_cell(str(path), cell_id=cell_id)

    assert cells[0]["id"] == cell_id
    assert by_id["cell_id"] == by_id["id"] == cell_id
    assert by_id["position"] == 0


def test_list_tools_document_their_result_wrapper():
    """FastMCP returns a tool's list as {"result": [...]}; Code Mode scripts
    see that dict, so the descriptions agents read must say so."""
    for tool in (view_notebook, list_notebook_sessions):
        assert '{"result": [' in tool.__doc__, tool.__name__
