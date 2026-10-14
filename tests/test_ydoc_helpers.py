"""Tests for ydoc_helpers — live YNotebook lookup and room recreation.

Room recreation goes through jupyter_server_ydoc's official
``YDocExtension.get_document(..., copy=False, create=True)``: it builds the
room from the same ystore (same file_id), initializes its content, and holds
the extension's per-room lock — the one the browser websocket handler uses.
get_document() initializes only the rooms it creates, so we also initialize
an existing room the browser handler registered but has not opened yet.
These tests pin the arguments we pass and the fallbacks around that call.
"""

import asyncio
from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jupyter_server_ydoc.utils import encode_file_path, room_id_from_encoded_path

from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.path_utils import normalize_path, to_api_path
from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook, get_ynotebook

_HELPERS = "jupyter_mcp_executor.ydoc_helpers"


class _FakeYNotebook:
    """Minimal YNotebook stand-in."""


class _FakeOtherDoc:
    """A non-YNotebook document type (should cause None return)."""


_ROOM_ID = room_id_from_encoded_path(encode_file_path("json", "notebook", "fid"))


def _make_room(*, ready: bool = True):
    """A DocumentRoom mock; initialize() marks it ready."""
    room = MagicMock()
    room.room_id = _ROOM_ID
    room.ready = ready

    async def initialize():
        room.ready = True

    room.initialize = AsyncMock(side_effect=initialize)
    return room


def _make_extension(*, document=None, started: bool = True, room=None):
    """A YDocExtension mock whose get_document returns ``document``.

    ``room`` is registered under the notebook's room id (file id "fid"); by
    default an already-initialized one.
    """
    ext = MagicMock()
    ext.get_document = AsyncMock(return_value=document)
    ext.ywebsocket_server.started.is_set.return_value = started
    ext.serverapp.web_app.settings = {"file_id_manager": MagicMock()}
    ext.serverapp.web_app.settings["file_id_manager"].index.return_value = "fid"
    ext.ywebsocket_server.rooms = {_ROOM_ID: room if room is not None else _make_room()}
    ext._room_locks = defaultdict(asyncio.Lock)
    # Rooms are never cleaned up, as with this upstream setting; the cleanup
    # is tested against a real server (integration/test_ydoc_room_cleanup.py).
    ext.document_cleanup_delay = None
    ext.document_save_delay = 1.0
    return ext


def _patches(ext, *, logger=None):
    """Common patches: YDoc available, fake YNotebook type, given extension."""
    patches = [
        patch(f"{_HELPERS}.YDOC_AVAILABLE", True),
        patch(f"{_HELPERS}.YNotebook", _FakeYNotebook),
        patch(f"{_HELPERS}._get_ydoc_extension", return_value=ext),
    ]
    if logger is not None:
        patches.append(patch(f"{_HELPERS}.logger", logger))
    return patches


async def _ensure(ext, path: str = "/tmp/nb.ipynb", *, logger=None):
    with ExitStack() as stack:
        for p in _patches(ext, logger=logger):
            stack.enter_context(p)
        return await ensure_ynotebook(path)


# ---------------------------------------------------------------------------
# ensure_ynotebook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_ydoc_unavailable():
    """When pycrdt / jupyter_ydoc are not installed, return None immediately."""
    with patch(f"{_HELPERS}.YDOC_AVAILABLE", False):
        assert await ensure_ynotebook("/tmp/nb.ipynb") is None


@pytest.mark.asyncio
async def test_returns_none_when_no_ydoc_extension():
    """If jupyter_server_ydoc is not enabled, return None without crashing."""
    with (
        patch(f"{_HELPERS}.YDOC_AVAILABLE", True),
        patch(f"{_HELPERS}._get_ydoc_extension", return_value=None),
    ):
        assert await ensure_ynotebook("/tmp/nb.ipynb") is None


@pytest.mark.asyncio
async def test_gets_or_creates_room_via_official_api():
    """The room is fetched — or recreated from the ystore — by get_document.

    copy=False returns the live document the browser syncs with (a copy would
    silently drop our edits); create=True recreates a room that was cleaned up
    after the browser disconnected, so writes never fall through to the file.
    """
    fake_ynb = _FakeYNotebook()
    ext = _make_extension(document=fake_ynb)

    result = await _ensure(ext, "/some/path/notebook.ipynb")

    assert result is fake_ynb
    ext.get_document.assert_awaited_once_with(
        # The API path; without a server root it is the path as given (with
        # the OS separator: backslashes on Windows).
        path=to_api_path("/some/path/notebook.ipynb"),
        content_type="notebook",
        file_format="json",
        copy=False,
        create=True,
    )


@pytest.mark.asyncio
async def test_returns_none_if_document_wrong_type():
    """A non-YNotebook document and no room: use the file."""
    ext = _make_extension(document=_FakeOtherDoc())
    ext.ywebsocket_server.rooms = {}

    assert await _ensure(ext) is None


@pytest.mark.asyncio
async def test_returns_none_if_get_document_returns_none():
    ext = _make_extension(document=None)
    ext.ywebsocket_server.rooms = {}

    assert await _ensure(ext) is None


@pytest.mark.asyncio
async def test_no_document_while_a_room_exists_raises():
    """None from get_document() is not proof the file is safe."""
    ext = _make_extension(document=None)

    with pytest.raises(RuntimeError, match="is open in JupyterLab"):
        await _ensure(ext)


@pytest.mark.asyncio
async def test_unexpected_error_without_a_room_is_logged_and_returns_none():
    """No room to conflict with: the file is safe, but the failure is visible
    at ERROR, never silent."""
    ext = _make_extension()
    ext.ywebsocket_server.rooms = {}
    ext.get_document.side_effect = RuntimeError("boom")
    log = MagicMock()

    assert await _ensure(ext, logger=log) is None
    log.error.assert_called_once()
    assert isinstance(log.error.call_args.kwargs["exc_info"], RuntimeError)


@pytest.mark.asyncio
async def test_unexpected_error_with_a_room_raises_instead_of_using_the_file():
    """The room's file watcher would load our file writes over the live
    document, discarding the browser's unsaved changes."""
    ext = _make_extension()
    ext.get_document.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="open in JupyterLab"):
        await _ensure(ext)


@pytest.mark.asyncio
async def test_missing_websocket_server_is_a_warning():
    """No websocket server means the YDoc stack is not set up: use the file."""
    ext = _make_extension()
    del ext.ywebsocket_server
    log = MagicMock()

    assert await _ensure(ext, logger=log) is None
    log.warning.assert_called_once()
    ext.get_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_key_error_with_a_room_raises_instead_of_using_the_file():
    """KeyError is not proof that YDoc is absent (e.g. a file loader removed
    by a concurrent room cleanup), so it gets the same room check."""
    ext = _make_extension()
    ext.get_document.side_effect = KeyError("fid")

    with pytest.raises(RuntimeError, match="is open in JupyterLab"):
        await _ensure(ext)


@pytest.mark.asyncio
async def test_unknown_room_state_raises_instead_of_using_the_file():
    """If the file ID lookup itself fails, whether a room exists is unknown:
    assume it may, rather than write the file behind a live room."""
    ext = _make_extension()
    index = ext.serverapp.web_app.settings["file_id_manager"].index
    index.side_effect = RuntimeError("database is locked")
    ext.get_document.side_effect = RuntimeError("database is locked")

    with pytest.raises(RuntimeError, match="may be open in JupyterLab"):
        await _ensure(ext, "/tmp/never-attached.ipynb")


@pytest.mark.asyncio
async def test_room_attached_before_is_known_without_a_file_id_lookup():
    """A notebook we attached to before is known to have a room even when
    the file ID lookup is what fails now."""
    ext = _make_extension(document=_FakeYNotebook())
    assert await _ensure(ext, "/tmp/known.ipynb") is not None

    index = ext.serverapp.web_app.settings["file_id_manager"].index
    index.side_effect = RuntimeError("database is locked")
    ext.get_document.side_effect = RuntimeError("database is locked")

    with pytest.raises(RuntimeError, match="is open in JupyterLab"):
        await _ensure(ext, "/tmp/known.ipynb")


@pytest.mark.asyncio
async def test_websocket_server_not_started_falls_back_quietly():
    """No browser has connected yet: use the file, no room, no ERROR traceback.

    jupyter_server_ydoc starts its websocket server when the first browser
    connects. Before that no client holds CRDT state, so the file adapter is
    safe — and creating rooms for headless sessions would leak them (rooms
    are only cleaned up when a websocket disconnects).
    """
    ext = _make_extension(started=False)
    log = MagicMock()

    assert await _ensure(ext, logger=log) is None
    ext.get_document.assert_not_awaited()
    log.exception.assert_not_called()
    log.error.assert_not_called()
    log.warning.assert_not_called()
    assert any(
        "no browser has connected" in call.args[0] for call in log.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_ready_room_is_not_reinitialized():
    room = _make_room(ready=True)
    ext = _make_extension(document=_FakeYNotebook(), room=room)

    assert await _ensure(ext) is not None
    room.initialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_unopened_room_is_initialized_under_the_handler_lock():
    """get_document() returns an existing room's document even before the
    browser handler's open() initialized it: an empty notebook whose edits get
    autosaved over the file. We initialize it first, holding the per-room lock
    the handler's open() takes, so the two never initialize concurrently."""
    fake_ynb = _FakeYNotebook()
    room = _make_room(ready=False)
    ext = _make_extension(document=fake_ynb, room=room)
    held = []

    async def initialize():
        held.append(ext._room_locks[_ROOM_ID].locked())
        room.ready = True

    room.initialize.side_effect = initialize

    assert await _ensure(ext) is fake_ynb
    room.initialize.assert_awaited_once()
    assert held == [True]


@pytest.mark.asyncio
async def test_room_initialization_failure_raises():
    """Never hand out the empty document of a room that failed to load, and
    never write the file behind it either."""
    room = _make_room(ready=False)
    room.initialize.side_effect = RuntimeError("cannot load")
    ext = _make_extension(document=_FakeYNotebook(), room=room)

    with pytest.raises(RuntimeError, match="cannot load"):
        await _ensure(ext)


@pytest.mark.asyncio
async def test_room_removed_after_lookup_returns_none():
    """The room was cleaned up right after get_document(): its document is
    detached from any room, so do not return it."""
    ext = _make_extension(document=_FakeYNotebook())
    ext.ywebsocket_server.rooms = {}

    assert await _ensure(ext) is None


@pytest.mark.asyncio
async def test_room_evicted_during_lookup_is_recreated():
    """Eviction between get_document() and the room check is a race, not a
    reason to use the file: the CRDT history exists, so look again."""
    ext = _make_extension(document=_FakeYNotebook())
    room = ext.ywebsocket_server.rooms.pop(_ROOM_ID)

    async def get_document(**_kwargs):
        if ext.get_document.await_count > 1:  # the room was recreated
            ext.ywebsocket_server.rooms[_ROOM_ID] = room
        return _FakeYNotebook()

    ext.get_document.side_effect = get_document

    assert await _ensure(ext) is not None
    assert ext.get_document.await_count == 2


@pytest.mark.asyncio
async def test_ensure_ynotebook_keeps_the_room_alive_for_writes():
    """Callers write to the returned document (e.g. the kernelspec on a
    kernel switch); a pending cleanup would cancel the room's autosave."""
    room = _make_room()
    room.cleaner.done.return_value = False
    ext = _make_extension(document=_FakeYNotebook(), room=room)

    assert await _ensure(ext) is not None
    room.cleaner.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# get_ynotebook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ynotebook_never_creates_a_room():
    fake_ynb = _FakeYNotebook()
    ext = _make_extension(document=fake_ynb)
    with ExitStack() as stack:
        for p in _patches(ext):
            stack.enter_context(p)
        result = await get_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    assert ext.get_document.await_args.kwargs["create"] is False
    assert ext.get_document.await_args.kwargs["copy"] is False


@pytest.mark.asyncio
async def test_get_ynotebook_initializes_unopened_room():
    room = _make_room(ready=False)
    ext = _make_extension(document=_FakeYNotebook(), room=room)
    with ExitStack() as stack:
        for p in _patches(ext):
            stack.enter_context(p)
        assert await get_ynotebook("/tmp/nb.ipynb") is not None

    room.initialize.assert_awaited_once()


# ---------------------------------------------------------------------------
# notebook_adapter integration: ensure_ynotebook is wired in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_notebook_adapter_uses_the_room_it_recreates():
    """get_notebook_adapter() must use ensure_notebook_room() (recreating a
    cleaned-up room), not a lookup that falls back to the file, and hand the
    room to the adapter so it can follow a replaced room.

    This is the guarantee that NbformatAdapter is never used while an active
    CRDT session exists in the ystore.
    """
    notebook_room = MagicMock()

    # YDocAdapter is a local import inside get_notebook_adapter(); patch at source.
    with (
        patch(
            "jupyter_mcp_executor.notebook_adapter.ensure_notebook_room",
            new=AsyncMock(return_value=notebook_room),
        ) as mock_ensure,
        patch("jupyter_mcp_executor.ydoc_adapter.YDocAdapter") as mock_ydoc_cls,
    ):
        await get_notebook_adapter("/tmp/nb.ipynb")

    mock_ensure.assert_awaited_once()
    mock_ydoc_cls.assert_called_once_with(
        notebook_room.ydoc, normalize_path("/tmp/nb.ipynb"), room=notebook_room
    )
