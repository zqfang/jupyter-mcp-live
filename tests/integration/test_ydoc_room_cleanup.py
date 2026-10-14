"""Rooms the MCP tools use without a browser must not live forever.

jupyter_server_ydoc deletes a room only after a browser's websocket closes.
A room we create while no browser has the notebook open, or one whose
cleanup we postponed by editing it after the browser left, would otherwise
stay in memory (with a 1 s file watcher) for the server's lifetime.
"""

import asyncio

import nbformat
import pytest
from jupyter_server_ydoc.utils import decode_file_path

from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.server_context import set_server_context
from jupyter_mcp_executor.ydoc_helpers import (
    CLEANUP_SAVE_MARGIN,
    ensure_notebook_room,
)

CLEANUP_DELAY = 0.2
# Longer than the cleanup delay: the cleanup must still wait for the save.
SAVE_DELAY = 0.4
EFFECTIVE_DELAY = max(CLEANUP_DELAY, SAVE_DELAY + CLEANUP_SAVE_MARGIN)


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
        "YDocExtension": {
            "document_cleanup_delay": CLEANUP_DELAY,
            "document_save_delay": SAVE_DELAY,
        },
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
def notebook_path(jp_root_dir):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("x = 1")]
    path = jp_root_dir / "nb.ipynb"
    nbformat.write(nb, path)
    return str(path)


def _is_live(extension, room) -> bool:
    return extension.ywebsocket_server.rooms.get(room.room_id) is room


def _file_id(room) -> str:
    return decode_file_path(room.room_id)[2]


async def _wait_until_deleted(extension, room, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while _is_live(extension, room):
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def test_room_created_without_a_browser_is_cleaned_up(
    ydoc_extension, notebook_path
):
    adapter = await get_notebook_adapter(notebook_path)
    assert [c.source for c in await adapter.get_all_cells()] == ["x = 1"]
    room = (await ensure_notebook_room(notebook_path)).room

    assert await _wait_until_deleted(ydoc_extension, room, EFFECTIVE_DELAY + 2)
    # Its file watcher goes with it, as when the handler cleans a room.
    assert _file_id(room) not in ydoc_extension.file_loaders


async def test_edits_reach_the_disk_before_the_room_is_cleaned_up(
    ydoc_extension, notebook_path
):
    """Deleting a room cancels its pending save; the cleanup must come later."""
    adapter = await get_notebook_adapter(notebook_path)
    await adapter.add_cell(CellContent(source="y = 2", cell_type="code"))
    room = (await ensure_notebook_room(notebook_path)).room

    assert await _wait_until_deleted(ydoc_extension, room, EFFECTIVE_DELAY + 2)
    on_disk = nbformat.read(notebook_path, as_version=4)
    assert [c.source for c in on_disk.cells] == ["x = 1", "y = 2"]


async def test_keeping_a_room_alive_postpones_its_cleanup(
    ydoc_extension, notebook_path
):
    """A long cell keeps its room alive (heartbeat) until it finishes."""
    adapter = await get_notebook_adapter(notebook_path)
    room = (await ensure_notebook_room(notebook_path)).room

    for _ in range(int(EFFECTIVE_DELAY * 2 / 0.2)):
        adapter.keep_alive()
        await asyncio.sleep(0.2)
    assert _is_live(ydoc_extension, room)

    assert await _wait_until_deleted(ydoc_extension, room, EFFECTIVE_DELAY + 2)


async def test_room_a_browser_joined_is_kept(ydoc_extension, notebook_path):
    adapter = await get_notebook_adapter(notebook_path)
    await adapter.get_all_cells()
    room = (await ensure_notebook_room(notebook_path)).room

    room.clients.add(object())  # what serve() does for a browser's websocket
    await asyncio.sleep(EFFECTIVE_DELAY + 0.5)

    assert _is_live(ydoc_extension, room)
    room.clients.clear()


async def test_browser_cancelling_the_cleanup_keeps_the_room(
    ydoc_extension, notebook_path
):
    """The handler's open() cancels a pending cleaner, ours included."""
    await get_notebook_adapter(notebook_path)
    room = (await ensure_notebook_room(notebook_path)).room
    assert room.cleaner is not None

    room.cleaner.cancel()
    await asyncio.sleep(EFFECTIVE_DELAY + 0.5)

    assert _is_live(ydoc_extension, room)


async def test_no_cleanup_without_autosave(ydoc_extension, notebook_path):
    """With autosave off, deleting the room would discard our edits."""
    ydoc_extension.document_save_delay = None
    adapter = await get_notebook_adapter(notebook_path)
    await adapter.add_cell(CellContent(source="y = 2", cell_type="code"))
    room = (await ensure_notebook_room(notebook_path)).room

    assert room.cleaner is None
    await asyncio.sleep(EFFECTIVE_DELAY + 0.5)
    assert _is_live(ydoc_extension, room)
