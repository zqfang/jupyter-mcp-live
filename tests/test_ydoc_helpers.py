"""Tests for ydoc_helpers — live YNotebook lookup and room recreation.

Room recreation goes through jupyter_server_ydoc's official
``YDocExtension.get_document(..., copy=False, create=True)``: it builds the
room from the same ystore (same file_id), initializes its content, and holds
the extension's per-room lock — the one the browser websocket handler uses.
These tests pin the arguments we pass and the fallbacks around that call.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook, get_ynotebook

_HELPERS = "jupyter_mcp_executor.ydoc_helpers"


class _FakeYNotebook:
    """Minimal YNotebook stand-in."""


class _FakeOtherDoc:
    """A non-YNotebook document type (should cause None return)."""


def _make_extension(*, document=None, started: bool = True):
    """A YDocExtension mock whose get_document returns ``document``."""
    ext = MagicMock()
    ext.get_document = AsyncMock(return_value=document)
    ext.ywebsocket_server.started.is_set.return_value = started
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
        path="/some/path/notebook.ipynb",
        content_type="notebook",
        file_format="json",
        copy=False,
        create=True,
    )


@pytest.mark.asyncio
async def test_returns_none_if_document_wrong_type():
    """If the room holds a non-YNotebook document, return None gracefully."""
    ext = _make_extension(document=_FakeOtherDoc())

    assert await _ensure(ext) is None


@pytest.mark.asyncio
async def test_returns_none_if_get_document_returns_none():
    ext = _make_extension(document=None)

    assert await _ensure(ext) is None


@pytest.mark.asyncio
async def test_unexpected_error_is_logged_and_returns_none():
    """A failure to create the room is visible at ERROR, never silent: falling
    back to the file would reintroduce the ystore/disk desync."""
    ext = _make_extension()
    ext.get_document.side_effect = RuntimeError("boom")
    log = MagicMock()

    assert await _ensure(ext, logger=log) is None
    log.exception.assert_called_once()


@pytest.mark.asyncio
async def test_missing_infrastructure_is_a_warning():
    """No file_id_manager (KeyError) means the YDoc stack is not set up."""
    ext = _make_extension()
    ext.get_document.side_effect = KeyError("file_id_manager")
    log = MagicMock()

    assert await _ensure(ext, logger=log) is None
    log.warning.assert_called_once()
    log.exception.assert_not_called()


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


# ---------------------------------------------------------------------------
# notebook_adapter integration: ensure_ynotebook is wired in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_notebook_adapter_calls_ensure_ynotebook():
    """get_notebook_adapter() must call ensure_ynotebook(), NOT get_ynotebook().

    This confirms room recreation is wired into the adapter factory — the
    guarantee that NbformatAdapter is never used while an active CRDT session
    exists in the ystore.
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
        await get_notebook_adapter("/tmp/nb.ipynb")

    mock_ensure.assert_awaited_once()
    mock_ydoc_cls.assert_called_once()
