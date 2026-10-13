"""YDoc collaborative editing helper utilities."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Check if YDoc is available
try:
    from jupyter_ydoc import YNotebook
    from pycrdt import Text as YText

    YDOC_AVAILABLE = True
except ImportError:
    YDOC_AVAILABLE = False
    YNotebook = None
    YText = None


def _get_ydoc_extension() -> Any | None:
    """Return the YDocExtension instance from the running Jupyter server, or None."""
    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    if not serverapp:
        return None
    extension_manager = getattr(serverapp, "extension_manager", None)
    if not extension_manager:
        return None
    ydoc_apps = extension_manager.extension_apps.get("jupyter_server_ydoc", set())
    if not ydoc_apps:
        return None
    return next(iter(ydoc_apps))


async def _get_live_document(
    ydoc_extension: Any, file_path: str, *, create: bool
) -> Any | None:
    """Return the room's live YNotebook via YDocExtension.get_document().

    copy=False returns the room's own document — the one browsers sync with —
    so changes to it propagate in real time. With create=True a missing room
    is created from the ystore under the extension's per-room lock (shared
    with the browser websocket handler) and initialized before it is returned.
    """
    ydoc = await ydoc_extension.get_document(
        path=file_path,
        content_type="notebook",
        file_format="json",
        copy=False,
        create=create,
    )
    if ydoc is None:
        return None
    if not isinstance(ydoc, YNotebook):
        logger.debug(
            f"get_document returned unexpected type {type(ydoc)} for {file_path}"
        )
        return None
    return ydoc


async def get_ynotebook(file_path: str) -> Any | None:
    """Get YNotebook instance if a live room already exists for this notebook.

    Returns None if no room is currently registered (room was cleaned up or
    notebook was never opened in JupyterLab).  Callers that want to keep the
    room alive even after the browser disconnects should use
    ``ensure_ynotebook()`` instead.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance or None if room does not exist
    """
    if not YDOC_AVAILABLE:
        return None

    ydoc_extension = _get_ydoc_extension()
    if not ydoc_extension:
        return None

    try:
        return await _get_live_document(ydoc_extension, file_path, create=False)
    except Exception as e:  # noqa: BLE001 - no YDoc means use the file adapter
        logger.debug(f"Failed to get YNotebook via YDocExtension.get_document: {e}")
        return None


async def ensure_ynotebook(file_path: str) -> Any | None:
    """Return a live YNotebook, recreating the YDoc room if the browser closed it.

    When the last browser WebSocket disconnects, jupyter_server_ydoc deletes
    the room after ``document_cleanup_delay`` seconds (default 60 s).  Any
    subsequent write that falls through to NbformatAdapter bypasses the ystore,
    desynchronising CRDT history from disk.  When the browser later reconnects,
    the new room detects the mismatch, resets CRDT state from disk, and the
    browser's stored state vector (auto-reconnect or IndexedDB) no longer
    matches — causing cells to appear duplicated.

    This function prevents that by recreating the room through
    ``get_document(create=True)``.  The new room uses the same ystore path
    (same ``file_id``), so the full CRDT op history is restored and the
    reconnecting browser's state vector stays valid.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance or None if YDoc infrastructure is unavailable
    """
    if not YDOC_AVAILABLE:
        return None

    ydoc_extension = _get_ydoc_extension()
    if not ydoc_extension:
        return None

    try:
        # jupyter_server_ydoc starts its websocket server when the first
        # browser connects.  Until then no client holds CRDT state for any
        # document, so the file adapter is safe.  Creating rooms here would
        # also leak them: rooms are only cleaned up when a websocket closes.
        if not ydoc_extension.ywebsocket_server.started.is_set():
            logger.debug(
                f"YDoc websocket server not started (no browser has connected "
                f"yet); using the file for {file_path}"
            )
            return None

        return await _get_live_document(ydoc_extension, file_path, create=True)

    except (AttributeError, KeyError) as e:
        # Infrastructure not present or misconfigured (e.g. no file ID
        # manager) — return None so the caller can decide how to proceed.
        logger.warning(f"YDoc infrastructure unavailable for {file_path}: {e}")
    except Exception:
        # Unexpected failure — log at ERROR so it is visible in production
        # rather than silently falling back to NbformatAdapter (which would
        # reintroduce the ystore/disk desync bug).
        logger.exception(f"Failed to get or recreate YDoc room for {file_path}")

    return None


def get_cell_index_by_id_ydoc(ydoc: Any, cell_id: str) -> int | None:
    """Find cell index by UUID in YDoc.

    Args:
        ydoc: YNotebook instance
        cell_id: Cell UUID to find

    Returns:
        Cell index or None if not found
    """
    try:
        for i in range(ydoc.cell_number):
            ycell = ydoc.ycells[i]
            if str(ycell.get("id", "")) == cell_id:
                return i
    except Exception as e:  # noqa: BLE001 - lookup miss is reported as None
        logger.debug(f"Error finding cell by ID in YDoc: {e}")

    return None


def get_cell_index_by_id_nbformat(nb: Any, cell_id: str) -> int | None:
    """Find cell index by UUID in nbformat notebook.

    Args:
        nb: Notebook object
        cell_id: Cell UUID to find

    Returns:
        Cell index or None if not found
    """
    try:
        for i, cell in enumerate(nb.cells):
            if cell.get("id") == cell_id:
                return i
    except Exception as e:  # noqa: BLE001 - lookup miss is reported as None
        logger.debug(f"Error finding cell by ID in notebook: {e}")

    return None
