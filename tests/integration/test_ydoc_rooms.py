"""ensure_ynotebook() against a real jupyter_server_ydoc.

The unit tests pin the arguments passed to ``YDocExtension.get_document``;
these check that the installed jupyter-collaboration actually honours them:
a headless call loads the notebook into a live, reusable room.
"""

import asyncio

import nbformat
import pytest

from jupyter_mcp_executor.server_context import set_server_context
from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook, get_ynotebook


@pytest.fixture
def jp_server_config():
    return {
        "ServerApp": {
            "jpserver_extensions": {
                "jupyter_server_fileid": True,
                "jupyter_server_ydoc": True,
            }
        }
    }


@pytest.fixture
def ydoc_extension(jp_serverapp):
    set_server_context(jp_serverapp)
    apps = jp_serverapp.extension_manager.extension_apps["jupyter_server_ydoc"]
    yield next(iter(apps))
    set_server_context(None)


@pytest.fixture
def notebook_path(jp_root_dir):
    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_markdown_cell("# Title"),
        nbformat.v4.new_code_cell("x = 1"),
    ]
    path = jp_root_dir / "nb.ipynb"
    nbformat.write(nb, path)
    return str(path)


async def _start_websocket_server(ydoc_extension) -> asyncio.Task:
    """What the first browser connection does in jupyter_server_ydoc."""
    server = ydoc_extension.ywebsocket_server
    task = asyncio.create_task(server.start())
    await server.started.wait()
    return task


async def test_no_room_before_any_browser_connects(ydoc_extension, notebook_path):
    assert await ensure_ynotebook(notebook_path) is None
    assert not ydoc_extension.ywebsocket_server.started.is_set()


async def test_creates_loaded_room_and_reuses_it(ydoc_extension, notebook_path):
    server_task = await _start_websocket_server(ydoc_extension)
    try:
        assert await get_ynotebook(notebook_path) is None

        ydoc = await ensure_ynotebook(notebook_path)

        # Loaded from disk, not an empty document (create=True initializes).
        assert ydoc is not None
        assert ydoc.cell_number == 2
        assert str(ydoc.ycells[1]["source"]) == "x = 1"
        # Live room, not a copy: later lookups return the same document.
        assert await ensure_ynotebook(notebook_path) is ydoc
        assert await get_ynotebook(notebook_path) is ydoc
    finally:
        await ydoc_extension.stop_extension()
        server_task.cancel()
