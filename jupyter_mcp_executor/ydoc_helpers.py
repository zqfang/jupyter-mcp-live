"""YDoc collaborative editing helper utilities."""

import asyncio
import logging
import weakref
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, TypeVar

from jupyter_mcp_executor.path_utils import to_api_path

logger = logging.getLogger(__name__)

# Room ID of each notebook we attached to, by normalized path. Lets
# _has_room() answer even when the file ID lookup is what just failed.
_known_room_ids: dict[str, str] = {}

# Attempts to attach a notebook's room before giving up. A room can be
# evicted between get_document() and our check; the next attempt recreates it.
_ATTACH_ATTEMPTS = 3

# Seconds a room's cleanup waits beyond its autosave delay: deleting a room
# cancels its pending save, so the save must have finished first. Also how
# long the cleanup is put off while a save is still in progress.
CLEANUP_SAVE_MARGIN = 1.0

_T = TypeVar("_T")


@dataclass
class _Cleanup:
    """Our pending cleanup of one room, shared by every NotebookRoom of it."""

    task: asyncio.Task
    deadline: float  # event loop time


_cleanups: "weakref.WeakKeyDictionary[Any, _Cleanup]" = weakref.WeakKeyDictionary()

# Rooms whose full state we wrote to their ystore (see _persist_room_state).
_persisted_rooms: "weakref.WeakSet[Any]" = weakref.WeakSet()

# Seconds to wait for a started room to observe its document.
_OBSERVED_TIMEOUT = 10.0


async def _run_to_completion(aw: Awaitable[_T]) -> _T:
    """Await ``aw``, finishing it even if the caller is cancelled meanwhile.

    The cancellation is re-raised once ``aw`` is done.
    """
    task = asyncio.ensure_future(aw)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return task.result()


def _is_saving(room: Any) -> bool:
    """Whether the room has an autosave pending or in progress."""
    saving = getattr(room, "_saving_document", None)
    return saving is not None and not saving.done()


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


@dataclass
class NotebookRoom:
    """A notebook's live YDoc room, as the MCP tools hold it during a call.

    A call can outlive the room: jupyter_server_ydoc deletes a room
    ``document_cleanup_delay`` seconds after the last browser left, and a
    stopped room's document is detached (no broadcast, ystore or autosave).
    Callers therefore keep the room alive while they use it and follow it if
    it was replaced anyway.
    """

    ydoc: Any
    room: Any
    extension: Any
    file_path: str

    def is_attached(self) -> bool:
        """Whether this is still the room the server serves the notebook from."""
        rooms = self.extension.ywebsocket_server.rooms
        return rooms.get(self.room.room_id) is self.room

    def keep_alive(self) -> None:
        """Postpone the room's cleanup while the tools are using it.

        Deleting a room cancels its pending save, so edits from the last save
        delay would never reach the disk a recreated room loads from. A
        pending cleanup by the handler (the last browser left) is cancelled,
        as a reconnecting browser does, and replaced by ours. Cheap enough to
        call for every streamed output: it only moves our deadline.
        """
        cleaner = self.room.cleaner
        if cleaner is None or cleaner.done() or self._is_ours(cleaner):
            self._schedule_cleanup()
            return
        cleaner.cancel()
        logger.debug(f"Kept the YDoc room of {self.file_path} alive")
        # The handler schedules its cleanup while the leaving websocket is
        # still in room.clients; ours must replace it all the same.
        self._schedule_cleanup(replacing=True)

    def clean_up_if_unused(self) -> None:
        """Schedule the cleanup of a room no browser uses and none will clean.

        jupyter_server_ydoc schedules a cleanup only when a browser's
        websocket closes, so a room created without a browser would stay for
        the server's lifetime. A pending cleanup is left as is: reads must not
        postpone it.
        """
        cleaner = self.room.cleaner
        if cleaner is None or cleaner.done():
            self._schedule_cleanup()

    def _is_ours(self, cleaner: asyncio.Task) -> bool:
        pending = _cleanups.get(self.room)
        return pending is not None and pending.task is cleaner

    def _schedule_cleanup(self, *, replacing: bool = False) -> None:
        """Delete the room once it has been unused for the cleanup delay.

        Uses the room's ``cleaner`` slot, so a browser joining in the meantime
        cancels it like the handler's own cleanup. Nothing is scheduled while
        a browser is connected (it schedules one when it leaves), when the
        server never cleans rooms, or when autosave is off: the room's edits
        would then be lost with it.

        Args:
            replacing: Replace the handler's cleanup, even if the leaving
                browser's websocket is still listed
        """
        cleanup_delay = self.extension.document_cleanup_delay
        save_delay = self.extension.document_save_delay
        if cleanup_delay is None or save_delay is None:
            return
        if self.room.clients and not replacing:
            return
        delay = max(cleanup_delay, save_delay + CLEANUP_SAVE_MARGIN)
        deadline = asyncio.get_running_loop().time() + delay
        pending = _cleanups.get(self.room)
        if (
            pending is not None
            and self.room.cleaner is pending.task
            and not pending.task.done()
        ):
            pending.deadline = deadline
            return
        task = asyncio.create_task(self._clean_up_when_due())
        _cleanups[self.room] = _Cleanup(task, deadline)
        self.room.cleaner = task

    async def _clean_up_when_due(self) -> None:
        """Wait for the (movable) deadline and any save, then delete the room."""
        loop = asyncio.get_running_loop()
        while True:
            pending = _cleanups.get(self.room)
            if pending is None or pending.task is not asyncio.current_task():
                return
            remaining = pending.deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
            elif _is_saving(self.room):
                pending.deadline = loop.time() + CLEANUP_SAVE_MARGIN
            else:
                break
        try:
            await self._delete_if_unused()
        except Exception:  # a task nobody awaits: log it
            logger.exception(f"Failed to clean up the YDoc room of {self.file_path}")

    async def _delete_if_unused(self) -> None:
        """What the websocket handler does after the last browser left.

        Once started, the deletion is finished even if a joining browser
        cancels us meanwhile: a half-stopped room would be gone from the
        server but keep its file watcher, and the lock would be released
        before the browser's open() could see the room was gone.
        """
        async with self.extension._room_locks[self.room.room_id]:
            if self.room.clients or not self.is_attached():
                return
            logger.info(f"Deleting the unused YDoc room of {self.file_path}")
            await _run_to_completion(self._delete())

    async def _delete(self) -> None:
        from jupyter_server_ydoc.utils import decode_file_path  # noqa: PLC0415

        try:
            await self.extension.ywebsocket_server.delete_room(room=self.room)
        finally:
            _known_room_ids.pop(self.file_path, None)
            file_id = decode_file_path(self.room.room_id)[2]
            loaders = self.extension.file_loaders
            if file_id in loaders and loaders[file_id].number_of_subscriptions == 0:
                await loaders.remove(file_id)

    async def reattach(self) -> "NotebookRoom | None":
        """Return the notebook's current room, recreating it if needed."""
        return await ensure_notebook_room(self.file_path)


async def _attach_room(
    ydoc_extension: Any, file_path: str, *, create: bool
) -> NotebookRoom | None:
    """Return the notebook's live, initialized room via get_document().

    The room is looked up by the notebook's API path, as JupyterLab does: the
    file ID manager matches absolute paths against the unresolved root_dir
    only, so our resolved path would name another room under a symlinked root.

    copy=False returns the room's own document — the one browsers sync with —
    so changes to it propagate in real time. With create=True a missing room
    is created from the ystore under the extension's per-room lock (shared
    with the browser websocket handler) and initialized before it is returned.
    An existing room is returned as is, so it is initialized here if needed.
    """
    api_path = to_api_path(file_path)
    if create:
        _configure_ystore_class(ydoc_extension)
    ydoc = await ydoc_extension.get_document(
        path=api_path,
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
    room = await _ensure_room_initialized(ydoc_extension, api_path)
    if room is None:
        return None
    await _persist_room_state(room)
    _known_room_ids[file_path] = room.room_id
    notebook_room = NotebookRoom(ydoc, room, ydoc_extension, file_path)
    notebook_room.clean_up_if_unused()
    return notebook_room


def _room_id(ydoc_extension: Any, api_path: str) -> str:
    """Return the YDoc room ID of a notebook (indexing it if needed)."""
    from jupyter_server_ydoc.utils import (  # noqa: PLC0415
        encode_file_path,
        room_id_from_encoded_path,
    )

    file_id_manager = ydoc_extension.serverapp.web_app.settings["file_id_manager"]
    file_id = file_id_manager.index(api_path)
    return room_id_from_encoded_path(encode_file_path("json", "notebook", file_id))


def _configure_ystore_class(ydoc_extension: Any) -> None:
    """Make get_document() build ystores from the server config.

    The websocket handler creates a room's ystore with the configured class
    (``partial(ystore_class, config=...)``), but get_document(create=True)
    instantiates the bare ``ystore_class`` (jupyter-collaboration 4.4.0 to
    5.0.4), so settings such as ``SQLiteYStore.db_path`` are ignored for the
    rooms it creates. Those rooms then read another database than browser
    rooms, and a reconnecting browser merges two unrelated CRDT histories
    (duplicated cells).

    get_document() is the only runtime reader of ``ystore_class``: the
    handlers received their ystore class at startup. Swapping in a subclass
    that injects the extension's config is therefore idempotent and local.
    """
    base = ydoc_extension.ystore_class
    if getattr(base, "_mcp_injects_config", False):
        return
    config = ydoc_extension.config

    class ConfiguredYStore(base):  # type: ignore[misc, valid-type]
        _mcp_injects_config = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("config", config)
            super().__init__(*args, **kwargs)

    ConfiguredYStore.__name__ = ConfiguredYStore.__qualname__ = base.__name__
    ydoc_extension.ystore_class = ConfiguredYStore


async def _ensure_room_initialized(ydoc_extension: Any, api_path: str) -> Any | None:
    """Load the notebook into its room if the browser handler has not yet.

    The websocket handler registers a room in prepare() but initializes it
    only in open(), and get_document() returns an existing room's document
    without checking. Until then the document is empty: reads see no cells,
    and edits to it are autosaved over the notebook on disk. initialize()
    runs under the per-room lock the handler's open() takes, so the two
    never load the room concurrently; it is a no-op once the room is ready.

    Returns:
        The room, or None if it was removed after get_document() returned
        (its document is detached).
    """
    room_id = _room_id(ydoc_extension, api_path)
    room = ydoc_extension.ywebsocket_server.rooms.get(room_id)
    if room is None:
        logger.debug(f"YDoc room for {api_path} was removed during lookup")
        return None
    if not room.ready:
        async with ydoc_extension._room_locks[room_id]:
            await room.initialize()
        logger.debug(f"Initialized YDoc room the browser had not opened: {api_path}")
    return room


async def _persist_room_state(room: Any) -> None:
    """Write the room's full state to its ystore, once per room.

    A room forwards its updates to the ystore only once its ``_watch_ready``
    task has observed the document, some time after ``ready`` is set. Ops of
    the room's own client before that are never stored: the ``dirty = False``
    DocumentRoom.initialize() writes before ``ready`` (jupyter-collaboration
    4.4), and our first edit after get_document(create=True) returned, which
    does not wait for the observer (4.4 and 5.0). Yjs integrates a client's
    ops in clock order, so every later op of that client then stays pending
    when the store is loaded. Our edits are that client's ops: a recreated
    room lacked them, reloaded the file over the store as new cells, and a
    reconnecting browser, which holds the originals, added them back
    (duplicated cells).

    Waits for the observer when the room tells (pycrdt-websocket >= 0.16.5),
    then stores everything before it. Must run before our first edit.
    """
    if room.ystore is None or room in _persisted_rooms:
        return
    observed = getattr(room, "ydoc_observed", None)
    if observed is not None:
        try:
            await asyncio.wait_for(observed.wait(), _OBSERVED_TIMEOUT)
        except TimeoutError:
            logger.warning(
                f"YDoc room {room.room_id} does not observe its document; "
                "edits may not reach its ystore"
            )
    await room.ystore.encode_state_as_update(room.ydoc)
    _persisted_rooms.add(room)


def _has_room(ydoc_extension: Any, file_path: str) -> bool | None:
    """Whether the notebook has a room now; None if that cannot be told.

    Checks the room we last attached to first, which needs no file ID lookup
    (the lookup may be what just failed).
    """
    try:
        rooms = ydoc_extension.ywebsocket_server.rooms
        known = _known_room_ids.get(file_path)
        if known is not None and known in rooms:
            return True
        return _room_id(ydoc_extension, to_api_path(file_path)) in rooms
    except Exception:  # noqa: BLE001 - reported as unknown
        return None


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
        notebook_room = await _attach_room(ydoc_extension, file_path, create=False)
    except Exception as e:  # noqa: BLE001 - no YDoc means use the file adapter
        logger.debug(f"Failed to get YNotebook via YDocExtension.get_document: {e}")
        return None
    return notebook_room.ydoc if notebook_room else None


async def ensure_ynotebook(file_path: str) -> Any | None:
    """Return a live YNotebook, recreating the YDoc room if the browser closed it.

    See ``ensure_notebook_room()``.

    The room is kept alive: callers write to the document (e.g. the
    kernelspec on a kernel switch), and a pending cleanup would cancel the
    room's autosave. Callers that hold the document across awaits should use
    ``ensure_notebook_room()`` and follow a replaced room instead.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance or None if YDoc infrastructure is unavailable

    Raises:
        RuntimeError: If the notebook has a room but it cannot be attached
    """
    notebook_room = await ensure_notebook_room(file_path)
    if notebook_room is None:
        return None
    notebook_room.keep_alive()
    return notebook_room.ydoc


async def ensure_notebook_room(file_path: str) -> NotebookRoom | None:
    """Return the notebook's live room, recreating it if the browser closed it.

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
        The live room, or None if the file should be used instead (no YDoc
        infrastructure, or no browser has connected yet)

    Raises:
        RuntimeError: If the notebook has a room but it cannot be attached.
            Writing the file behind a live room is not an option: the room's
            file watcher loads the file over the live document, discarding
            the browser's unsaved changes.
    """
    if not YDOC_AVAILABLE:
        return None

    ydoc_extension = _get_ydoc_extension()
    if not ydoc_extension:
        return None

    # jupyter_server_ydoc starts its websocket server when the first browser
    # connects.  Until then no client holds CRDT state for any document, so
    # the file adapter is safe, and cheaper than loading a room.
    try:
        started = ydoc_extension.ywebsocket_server.started.is_set()
    except AttributeError as e:
        logger.warning(f"YDoc infrastructure unavailable for {file_path}: {e}")
        return None
    if not started:
        logger.debug(
            f"YDoc websocket server not started (no browser has connected "
            f"yet); using the file for {file_path}"
        )
        return None

    error: Exception | None = None
    try:
        for _ in range(_ATTACH_ATTEMPTS):
            notebook_room = await _attach_room(ydoc_extension, file_path, create=True)
            if notebook_room is not None:
                return notebook_room
            # No document, or the room was evicted right after get_document()
            # returned it (a race): look again, which recreates the room.
    except Exception as e:  # noqa: BLE001 - re-raised below unless no room
        error = e

    has_room = _has_room(ydoc_extension, file_path)
    if has_room is False:
        # No room to conflict with: the file adapter is safe, but log the
        # failure at ERROR so it is visible in production.
        logger.error(
            f"No live document for {file_path}; using the file", exc_info=error
        )
        return None
    state = (
        "is open in JupyterLab"
        if has_room
        else "may be open in JupyterLab (its room could not be checked)"
    )
    reason = repr(error) if error else "no live document was returned"
    msg = (
        f"{file_path} {state}, but its live document could not be attached "
        f"({reason}). Not editing the file instead: that would discard unsaved "
        "changes in the browser."
    )
    raise RuntimeError(msg) from error


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
