"""ensure_ynotebook() against a real jupyter_server_ydoc.

The unit tests pin the arguments passed to ``YDocExtension.get_document``;
these check that the installed jupyter-collaboration actually honours them:
a headless call loads the notebook into a live, reusable room.
"""

import asyncio

import nbformat
import pytest
from jupyter_server_ydoc.rooms import DocumentRoom
from jupyter_server_ydoc.utils import encode_file_path, room_id_from_encoded_path

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


async def _register_unopened_room(ydoc_extension, notebook_path) -> DocumentRoom:
    """What YDocWebSocketHandler.prepare() does: create and add a room.

    The handler only initializes the room later, in open(), so until then the
    room's document is empty.
    """
    serverapp = ydoc_extension.serverapp
    file_id = serverapp.web_app.settings["file_id_manager"].index(notebook_path)
    room_id = room_id_from_encoded_path(encode_file_path("json", "notebook", file_id))
    room = DocumentRoom(
        room_id,
        "json",
        "notebook",
        ydoc_extension.file_loaders[file_id],
        serverapp.event_logger,
        ydoc_extension.ystore_class(
            path=f".notebook:{file_id}.y", log=ydoc_extension.log
        ),
        ydoc_extension.log,
        save_delay=ydoc_extension.document_save_delay,
    )
    await ydoc_extension.ywebsocket_server.start_room(room)
    ydoc_extension.ywebsocket_server.add_room(room_id, room)
    return room


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


@pytest.mark.parametrize("lookup", [ensure_ynotebook, get_ynotebook])
async def test_initializes_room_the_browser_has_not_opened_yet(
    ydoc_extension, notebook_path, lookup
):
    """A room the websocket handler registered but has not initialized yet.

    get_document() initializes only the rooms it creates itself, so without
    our readiness check this returned the room's still-empty document: reads
    saw 0 cells, and edits were autosaved over the notebook on disk.
    """
    server_task = await _start_websocket_server(ydoc_extension)
    try:
        room = await _register_unopened_room(ydoc_extension, notebook_path)
        assert not room.ready

        ydoc = await lookup(notebook_path)

        assert ydoc is room._document
        assert room.ready
        assert ydoc.cell_number == 2
        assert str(ydoc.ycells[1]["source"]) == "x = 1"
    finally:
        await ydoc_extension.stop_extension()
        server_task.cancel()
