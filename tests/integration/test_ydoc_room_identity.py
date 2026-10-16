"""The MCP tools must write to the room JupyterLab is showing.

Real jupyter_server_ydoc, with the server root behind a symlink (macOS /tmp
and /var, symlinked homes, container volumes). JupyterLab names a notebook by
its API path; the file ID manager matches absolute paths against the
unresolved root_dir only, so a resolved absolute path used to get a different
file ID, i.e. a different room.
"""

import asyncio

import nbformat
import pytest
from jupyter_server_ydoc.rooms import DocumentRoom
from jupyter_server_ydoc.utils import encode_file_path, room_id_from_encoded_path

from jupyter_mcp_live.notebook_adapter import get_notebook_adapter
from jupyter_mcp_live.notebook_models import CellContent
from jupyter_mcp_live.path_utils import normalize_path
from jupyter_mcp_live.server_context import set_server_context
from jupyter_mcp_live.ydoc_helpers import ensure_ynotebook

API_PATH = "nb.ipynb"


@pytest.fixture
def jp_root_dir(tmp_path):
    real = tmp_path / "real_root"
    real.mkdir()
    link = tmp_path / "root_link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    return link


@pytest.fixture
def jp_server_config(tmp_path):
    return {
        "ServerApp": {
            "jpserver_extensions": {
                "jupyter_server_fileid": True,
                "jupyter_server_ydoc": True,
            }
        },
        "BaseFileIdManager": {"db_path": ":memory:"},
        "SQLiteYStore": {"db_path": str(tmp_path / "ystore.db")},
    }


@pytest.fixture
def ydoc_extension(jp_serverapp, jp_asyncio_loop):
    """The YDoc extension with its websocket server started (a browser came by)."""
    set_server_context(jp_serverapp)
    apps = jp_serverapp.extension_manager.extension_apps["jupyter_server_ydoc"]
    extension = next(iter(apps))
    server = extension.ywebsocket_server
    server_task = jp_asyncio_loop.create_task(server.start())
    jp_asyncio_loop.run_until_complete(server.started.wait())
    yield extension
    jp_asyncio_loop.run_until_complete(extension.stop_extension())
    server_task.cancel()
    set_server_context(None)


@pytest.fixture
def notebook_path(jp_root_dir, ydoc_extension):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("x = 1")]
    nbformat.write(nb, jp_root_dir / API_PATH)
    # What every tool does with the path it receives.
    return normalize_path(API_PATH)


async def _open_in_browser(extension) -> DocumentRoom:
    """What the websocket handler does for a JupyterLab tab (prepare + open).

    JupyterLab asks the file ID service for the notebook's API path.
    """
    serverapp = extension.serverapp
    file_id = serverapp.web_app.settings["file_id_manager"].index(API_PATH)
    room_id = room_id_from_encoded_path(encode_file_path("json", "notebook", file_id))
    room = DocumentRoom(
        room_id,
        "json",
        "notebook",
        extension.file_loaders[file_id],
        serverapp.event_logger,
        extension.ywebsocket_server.ystore_class(
            path=f".notebook:{file_id}.y", log=extension.log
        ),
        extension.log,
        save_delay=extension.document_save_delay,
    )
    await extension.ywebsocket_server.start_room(room)
    extension.ywebsocket_server.add_room(room_id, room)
    await room.initialize()
    return room


async def _evict(extension, room: DocumentRoom) -> None:
    """What the handler's cleanup does once the last browser left."""
    await extension.ywebsocket_server.delete_room(room=room)


def _sources(ydoc) -> list[str]:
    return [str(ydoc.ycells[i]["source"]) for i in range(ydoc.cell_number)]


async def test_finds_the_browsers_room_under_a_symlinked_root(
    ydoc_extension, notebook_path
):
    room = await _open_in_browser(ydoc_extension)

    assert await ensure_ynotebook(notebook_path) is room._document


def _schedule_cleanup(extension, room: DocumentRoom) -> asyncio.Task:
    """What the handler does when the last browser leaves (delay shortened)."""

    async def cleanup():
        await asyncio.sleep(0.3)
        await _evict(extension, room)

    room.cleaner = asyncio.create_task(cleanup())
    return room.cleaner


async def test_editing_a_room_cancels_its_pending_cleanup(
    ydoc_extension, notebook_path
):
    """The browser left; the room must not be evicted while we edit it.

    Eviction cancels the room's pending save, so edits from the last save
    delay would never reach the disk the next room loads from.
    """
    room = await _open_in_browser(ydoc_extension)
    cleanup = _schedule_cleanup(ydoc_extension, room)

    adapter = await get_notebook_adapter(notebook_path)
    await adapter.add_cell(CellContent(source="y = 2", cell_type="code"))
    await asyncio.wait({cleanup}, timeout=2)

    assert cleanup.cancelled()
    assert ydoc_extension.ywebsocket_server.rooms.get(room.room_id) is room


async def test_reading_does_not_pin_the_room(ydoc_extension, notebook_path):
    """Viewing a notebook must not keep its room for the server's lifetime."""
    room = await _open_in_browser(ydoc_extension)
    cleanup = _schedule_cleanup(ydoc_extension, room)

    adapter = await get_notebook_adapter(notebook_path)
    assert [c.source for c in await adapter.get_all_cells()] == ["x = 1"]
    await asyncio.wait({cleanup}, timeout=2)

    assert not cleanup.cancelled()
    assert room.room_id not in ydoc_extension.ywebsocket_server.rooms


async def test_adapter_follows_a_room_that_was_replaced(ydoc_extension, notebook_path):
    """An adapter outlives its room (long cell, browser closed): writes must
    reach the live room, not the stopped room's detached document."""
    room = await _open_in_browser(ydoc_extension)
    adapter = await get_notebook_adapter(notebook_path)
    await _evict(ydoc_extension, room)

    await adapter.add_cell(CellContent(source="y = 2", cell_type="code"))

    live = await ensure_ynotebook(notebook_path)
    assert live is not room._document
    assert _sources(live) == ["x = 1", "y = 2"]
    assert adapter.generation == 1


async def test_no_file_fallback_while_the_notebook_has_a_room(
    ydoc_extension, notebook_path, monkeypatch
):
    """A failed lookup must not turn into writes to the .ipynb: the room's
    file watcher would overwrite the live document with them."""
    await _open_in_browser(ydoc_extension)

    async def broken_get_document(**_kwargs):
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(ydoc_extension, "get_document", broken_get_document)

    with pytest.raises(RuntimeError, match="open in JupyterLab"):
        await get_notebook_adapter(notebook_path)
