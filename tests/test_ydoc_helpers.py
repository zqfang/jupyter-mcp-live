"""TDD tests for ydoc_helpers — ensure_ynotebook room-recreation logic.

Focus: the boundary conditions that prevent the ystore/disk desync bug
that causes cell duplication on browser reconnect after network loss.

Patching note: ensure_ynotebook() uses local imports (# noqa: PLC0415) to
avoid circular imports, so we patch at source-module level:
  - jupyter_mcp_executor.server_context.get_server_context
  - jupyter_server_ydoc.rooms.DocumentRoom
  - jupyter_server_ydoc.utils.encode_file_path / room_id_from_encoded_path
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

class _FakeYNotebook:
    """Minimal YNotebook stand-in."""


class _FakeOtherDoc:
    """A non-YNotebook document type (should cause None return)."""


def _make_extension(
    *,
    room_exists: bool = False,
    room_doc=None,
    ystore_class=None,
    file_loaders=None,
    document_save_delay: int = 1,
):
    """Build a minimal ydoc_extension mock."""
    room_doc = room_doc or _FakeYNotebook()
    if file_loaders is None:
        file_loaders = MagicMock()
        file_loaders.__getitem__ = MagicMock(return_value=MagicMock())

    room = MagicMock()
    room._document = room_doc

    ws_server = MagicMock()
    ws_server.room_exists.return_value = room_exists
    ws_server.get_room = AsyncMock(return_value=room)
    ws_server.start_room = AsyncMock()
    ws_server.add_room = MagicMock()
    ws_server.ystore_class = ystore_class or MagicMock(return_value=MagicMock())

    ext = MagicMock()
    ext.ywebsocket_server = ws_server
    ext.file_loaders = file_loaders
    ext.document_save_delay = document_save_delay
    ext.event_logger = MagicMock()
    ext.log = MagicMock()
    return ext, room, ws_server


def _make_serverapp(file_id: str = "abc123"):
    app = MagicMock()
    app.web_app.settings = {
        "file_id_manager": MagicMock(index=MagicMock(return_value=file_id))
    }
    app.event_logger = MagicMock()
    return app


# ---------------------------------------------------------------------------
# Shared patch targets for local imports inside ensure_ynotebook()
# ---------------------------------------------------------------------------
_SC = "jupyter_mcp_executor.server_context.get_server_context"
_DR = "jupyter_server_ydoc.rooms.DocumentRoom"
_EFP = "jupyter_server_ydoc.utils.encode_file_path"
_RIFEP = "jupyter_server_ydoc.utils.room_id_from_encoded_path"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_ydoc_unavailable():
    """When pycrdt / jupyter_ydoc are not installed, return None immediately."""
    with patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", False):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")
    assert result is None


@pytest.mark.asyncio
async def test_fast_path_returns_existing_live_room():
    """If the room is already alive, return the document without recreating."""
    fake_ynb = _FakeYNotebook()

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=fake_ynb)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension") as mock_ext,
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    # Should NOT attempt to access the extension (fast path exits early)
    mock_ext.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_no_ydoc_extension():
    """If the YDocExtension is not registered, return None without crashing."""
    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=None),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is None


@pytest.mark.asyncio
async def test_recreates_room_when_gone_and_returns_document():
    """Core bug-fix path: room was cleaned up; recreate it from ystore."""
    file_id = "deadbeef"
    fake_ynb = _FakeYNotebook()
    ext, room, ws_server = _make_extension(room_exists=False, room_doc=fake_ynb)
    serverapp = _make_serverapp(file_id=file_id)

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_DR, MagicMock(return_value=room)),
        patch(_EFP, return_value="encoded"),
        patch(_RIFEP, return_value="room-1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    ws_server.start_room.assert_awaited_once_with(room)
    ws_server.add_room.assert_called_once_with("room-1", room)


@pytest.mark.asyncio
async def test_race_condition_uses_existing_room_if_created_concurrently():
    """If another coroutine created the room (race), reuse it — no double start."""
    file_id = "deadbeef"
    fake_ynb = _FakeYNotebook()
    ext, room, ws_server = _make_extension(room_exists=True, room_doc=fake_ynb)
    serverapp = _make_serverapp(file_id=file_id)

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_DR) as mock_dr,
        patch(_EFP, return_value="encoded"),
        patch(_RIFEP, return_value="room-1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    # DocumentRoom constructor must NOT be called — room already exists
    mock_dr.assert_not_called()
    ws_server.start_room.assert_not_awaited()
    ws_server.add_room.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_if_room_document_wrong_type():
    """If the room holds a non-YNotebook document, return None gracefully."""
    file_id = "abc"
    bad_doc = _FakeOtherDoc()
    ext, room, ws_server = _make_extension(room_exists=False, room_doc=bad_doc)
    serverapp = _make_serverapp(file_id=file_id)

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_DR, MagicMock(return_value=room)),
        patch(_EFP, return_value="enc"),
        patch(_RIFEP, return_value="r1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_exception_during_room_recreation():
    """Any unexpected error during room recreation is swallowed; None is returned."""
    ext, _room, ws_server = _make_extension(room_exists=False)
    ws_server.room_exists.side_effect = RuntimeError("boom")
    serverapp = _make_serverapp()

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_EFP, return_value="enc"),
        patch(_RIFEP, return_value="r1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is None


@pytest.mark.asyncio
async def test_ystore_uses_file_id_for_path():
    """The ystore path must use the stable file_id, not the raw filesystem path.

    This is the invariant that makes room recreation load the same CRDT
    history the original room used, preventing ystore/disk desync.
    """
    file_id = "stable-file-id-42"
    fake_ynb = _FakeYNotebook()
    ext, room, ws_server = _make_extension(room_exists=False, room_doc=fake_ynb)
    serverapp = _make_serverapp(file_id=file_id)

    captured = {}

    def fake_ystore_class(path):
        captured["path"] = path
        return MagicMock()

    ext.ywebsocket_server.ystore_class = fake_ystore_class

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_DR, MagicMock(return_value=room)),
        patch(_EFP, return_value="enc"),
        patch(_RIFEP, return_value="r1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        await ensure_ynotebook("/some/path/notebook.ipynb")

    assert captured.get("path") == f".notebook:{file_id}.y"


@pytest.mark.asyncio
async def test_recreated_room_is_initialized_to_load_content():
    """Regression: a recreated room must be initialize()'d so its YDoc loads
    content from the ystore/disk.

    start_room() only starts the broadcast loop; jupyter_server_ydoc loads the
    document via DocumentRoom.initialize(), which it otherwise calls only from
    its websocket handler when a browser connects.  Without this, a headless
    (no-browser) room stays EMPTY — reads return 0 cells and a save overwrites
    the on-disk notebook with a blank document.
    """
    file_id = "deadbeef"
    fake_ynb = _FakeYNotebook()
    ext, room, ws_server = _make_extension(room_exists=False, room_doc=fake_ynb)
    serverapp = _make_serverapp(file_id=file_id)
    room.ready = False
    room.initialize = AsyncMock()

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_DR, MagicMock(return_value=room)),
        patch(_EFP, return_value="encoded"),
        patch(_RIFEP, return_value="room-1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    room.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_room_is_not_reinitialized():
    """An already-ready room (live browser session) must NOT be re-initialized,
    so we never clobber in-flight collaborative edits."""
    file_id = "deadbeef"
    fake_ynb = _FakeYNotebook()
    ext, room, ws_server = _make_extension(room_exists=True, room_doc=fake_ynb)
    serverapp = _make_serverapp(file_id=file_id)
    room.ready = True
    room.initialize = AsyncMock()

    with (
        patch("jupyter_mcp_executor.ydoc_helpers.YDOC_AVAILABLE", True),
        patch("jupyter_mcp_executor.ydoc_helpers.YNotebook", _FakeYNotebook),
        patch("jupyter_mcp_executor.ydoc_helpers.get_ynotebook", new=AsyncMock(return_value=None)),
        patch("jupyter_mcp_executor.ydoc_helpers._get_ydoc_extension", return_value=ext),
        patch(_SC, return_value=serverapp),
        patch(_EFP, return_value="encoded"),
        patch(_RIFEP, return_value="room-1"),
    ):
        from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook
        result = await ensure_ynotebook("/tmp/nb.ipynb")

    assert result is fake_ynb
    room.initialize.assert_not_awaited()
    # existing room reused — not recreated
    ws_server.start_room.assert_not_awaited()


# ---------------------------------------------------------------------------
# notebook_adapter integration: ensure_ynotebook is wired in
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_notebook_adapter_calls_ensure_ynotebook():
    """get_notebook_adapter() must call ensure_ynotebook(), NOT get_ynotebook().

    This confirms the fix is wired into the adapter factory — the guarantee
    that NbformatAdapter is never used while an active CRDT session exists in
    the ystore.
    """
    fake_ynb = _FakeYNotebook()

    # YDocAdapter is a local import inside get_notebook_adapter(); patch at source.
    with (
        patch(
            "jupyter_mcp_executor.notebook_adapter.ensure_ynotebook",
            new=AsyncMock(return_value=fake_ynb),
        ) as mock_ensure,
        patch("jupyter_mcp_executor.ydoc_adapter.YDocAdapter") as mock_ydoc_cls,
    ):
        from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
        await get_notebook_adapter("/tmp/nb.ipynb")

    mock_ensure.assert_awaited_once()
    mock_ydoc_cls.assert_called_once()
