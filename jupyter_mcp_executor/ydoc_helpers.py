"""YDoc collaborative editing helper utilities."""

import asyncio
import logging
import weakref
from typing import Any

logger = logging.getLogger(__name__)

# Per-room_id locks that prevent TOCTOU races in ensure_ynotebook().
# WeakValueDictionary releases entries automatically once no coroutine holds
# a reference to the lock, preventing unbounded growth in long-running servers.
_room_creation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)

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


async def get_ynotebook(file_path: str) -> Any | None:
    """Get YNotebook instance if a live room already exists for this notebook.

    Uses the official YDocExtension.get_document() API from jupyter_server_ydoc.
    Returns None if no room is currently registered (room was cleaned up or
    notebook was never opened in JupyterLab).  Callers that want to keep the
    room alive even after the browser disconnects should use
    ``ensure_ynotebook()`` instead.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance (copy=False) or None if room does not exist
    """
    if not YDOC_AVAILABLE:
        return None

    ydoc_extension = _get_ydoc_extension()
    if not ydoc_extension:
        return None

    try:
        # get_document with copy=False returns the live room._document (YNotebook)
        # that the browser is synced with — changes to it propagate in real time.
        ydoc = await ydoc_extension.get_document(
            path=file_path,
            content_type="notebook",
            file_format="json",
            copy=False,
        )

        if ydoc and isinstance(ydoc, YNotebook):
            logger.debug(f"YNotebook found for {file_path}")
            return ydoc

        if ydoc is not None:
            logger.debug(
                f"get_document returned unexpected type {type(ydoc)} for {file_path}"
            )

    except Exception as e:
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

    This function prevents that by recreating the DocumentRoom programmatically
    when the room is gone.  The new room is initialised from the same ystore
    path (same ``file_id``), so the full CRDT op history is restored and the
    reconnecting browser's state vector stays valid.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance or None if YDoc infrastructure is unavailable
    """
    if not YDOC_AVAILABLE:
        return None

    # Fast path: room is already alive
    ydoc = await get_ynotebook(file_path)
    if ydoc is not None:
        return ydoc

    # Room is gone.  Recreate it so we never fall back to NbformatAdapter.
    ydoc_extension = _get_ydoc_extension()
    if not ydoc_extension:
        return None

    try:
        from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415
        from jupyter_server_ydoc.rooms import DocumentRoom  # noqa: PLC0415
        from jupyter_server_ydoc.utils import (  # noqa: PLC0415
            encode_file_path,
            room_id_from_encoded_path,
        )

        serverapp = get_server_context()
        file_id_manager = serverapp.web_app.settings["file_id_manager"]

        # Use the same file_id the original room used.  index() is idempotent
        # for the same path within a server session, so the ystore path matches.
        file_id = file_id_manager.index(file_path)
        encoded_path = encode_file_path("json", "notebook", file_id)
        room_id = room_id_from_encoded_path(encoded_path)

        ywebsocket_server = ydoc_extension.ywebsocket_server

        # Acquire a per-room_id lock to prevent TOCTOU races: two concurrent
        # callers that both observe room_exists()==False would otherwise each
        # create a DocumentRoom and call add_room(), resulting in duplicate
        # rooms with diverging CRDT state.
        # Keep a local strong reference so the WeakValueDictionary entry is
        # not garbage-collected between setdefault() and the first await.
        lock = _room_creation_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            _room_creation_locks[room_id] = lock
        async with lock:
            if ywebsocket_server.room_exists(room_id):
                room = await ywebsocket_server.get_room(room_id)
            else:
                # ystore path mirrors what DocumentRoom construction uses in handlers.py
                updates_file_path = f".notebook:{file_id}.y"
                ystore = ywebsocket_server.ystore_class(path=updates_file_path)

                room = DocumentRoom(
                    room_id=room_id,
                    file_format="json",
                    file_type="notebook",
                    file=ydoc_extension.file_loaders[file_id],
                    logger=serverapp.event_logger,
                    ystore=ystore,
                    log=ydoc_extension.log,
                    save_delay=ydoc_extension.document_save_delay,
                )

                # start_room calls room.initialize() which loads from ystore first
                # (preserving the original CRDT op history), falling back to disk
                # only if the ystore is absent or stale.
                await ywebsocket_server.start_room(room)
                ywebsocket_server.add_room(room_id, room)
                logger.info(
                    f"Recreated YDoc room for {file_path} to preserve CRDT history"
                )

        document = room._document
        if isinstance(document, YNotebook):
            return document

    except (ImportError, AttributeError, KeyError) as e:
        # Infrastructure not present or misconfigured — return None so the
        # caller can decide how to proceed.  These are expected in environments
        # without jupyter_server_ydoc.
        logger.warning(f"YDoc infrastructure unavailable for {file_path}: {e}")
    except Exception as e:
        # Unexpected failure — log at ERROR so it is visible in production
        # rather than silently falling back to NbformatAdapter (which would
        # reintroduce the ystore/disk desync bug).
        logger.error(f"Failed to recreate YDoc room for {file_path}: {e}", exc_info=True)

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
    except Exception as e:
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
    except Exception as e:
        logger.debug(f"Error finding cell by ID in notebook: {e}")

    return None
