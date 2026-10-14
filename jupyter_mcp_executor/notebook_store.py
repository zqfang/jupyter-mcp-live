"""Reading and saving notebooks that are not open in JupyterLab.

With a running server this goes through Jupyter's contents manager, as
JupyterLab's own save does: the notebook is signed when every risky output is
trusted, its first checkpoint is created, pre/post-save hooks run (e.g.
jupytext pairing), the write is atomic, and the contents manager's rules
(``allow_hidden``, non-filesystem backends) apply. Without a server (tests,
library use) the file is read and written with nbformat.

Open notebooks are never saved here: their YDoc room owns the file (see
``ydoc_adapter.py``).
"""

import asyncio
import os
import weakref
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import nbformat
from jupyter_core.utils import ensure_async
from tornado.web import HTTPError

from jupyter_mcp_executor.path_utils import to_api_path
from jupyter_mcp_executor.server_context import get_server_context

# One lock per notebook being edited, alive while someone holds or waits on it.
_edit_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _contents_manager() -> Any | None:
    serverapp = get_server_context()
    return getattr(serverapp, "contents_manager", None) if serverapp else None


async def _call(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a contents manager method on the event loop, as jupyter_server does.

    Not in a worker thread: a sync contents manager's notary keeps a SQLite
    connection that only the thread that created it may use. A sync manager
    therefore blocks the loop, exactly as it does for JupyterLab's requests;
    the default async one reads and writes files in worker threads itself.
    """
    return await ensure_async(method(*args, **kwargs))


def _api_path(path: str) -> str:
    """The contents manager path of a normalized notebook path.

    Raises:
        ValueError: If the path is outside the Jupyter root; a contents
            manager would read it as relative to the root instead
    """
    api_path = to_api_path(path)
    # Any root or drive (anchor) means it is not relative. On Windows a path
    # starting with a backslash has no drive, so it is not absolute, but it is
    # rooted all the same.
    if Path(api_path).anchor or api_path.startswith(("/", "\\")):
        msg = f"{path} is outside the Jupyter root directory"
        raise ValueError(msg)
    return api_path


async def load_notebook(path: str) -> Any:
    """Return the notebook at ``path`` (normalized) as a NotebookNode.

    Through a contents manager, code cells carry ``metadata.trusted`` (whether
    the notebook's signature is valid); ``save_notebook`` signs it again only
    if every risky output is still trusted. Checking the signature costs what
    JupyterLab pays to open the notebook (an HMAC over it).

    Raises:
        FileNotFoundError: If there is no such notebook, or the contents
            manager refuses to open it (e.g. hidden)
    """
    contents_manager = _contents_manager()
    if contents_manager is None:
        return await asyncio.to_thread(nbformat.read, path, as_version=4)
    api_path = _api_path(path)
    try:
        model = await _call(
            contents_manager.get, api_path, content=True, type="notebook"
        )
    except HTTPError as e:
        if e.status_code != 404:
            raise
        msg = f"No such notebook: {api_path} ({e.log_message})"
        raise FileNotFoundError(msg) from e
    return model["content"]


async def save_notebook(path: str, nb: Any) -> None:
    """Save ``nb`` to ``path`` (normalized), creating the file if needed."""
    contents_manager = _contents_manager()
    if contents_manager is None:
        await asyncio.to_thread(nbformat.write, nb, path)
        return
    model = {"type": "notebook", "format": "json", "content": nb}
    await _call(contents_manager.save, model, _api_path(path))


async def notebook_exists(path: str) -> bool:
    """Whether a notebook exists at ``path`` (normalized)."""
    contents_manager = _contents_manager()
    if contents_manager is None:
        return await asyncio.to_thread(os.path.isfile, path)
    return bool(await _call(contents_manager.file_exists, _api_path(path)))


async def ensure_directory(path: str) -> None:
    """Create the directory ``path`` (normalized) and its missing parents."""
    contents_manager = _contents_manager()
    if contents_manager is None:
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
        return
    parts = [p for p in _api_path(path).split("/") if p]
    for i in range(1, len(parts) + 1):
        directory = "/".join(parts[:i])
        if not await _call(contents_manager.dir_exists, directory):
            await _call(contents_manager.save, {"type": "directory"}, directory)


@asynccontextmanager
async def editing_notebook(path: str) -> AsyncIterator[Any]:
    """Load the notebook, let the caller change it, and save it on clean exit.

    Edits of one notebook are serialized: two concurrent read-modify-write
    calls (e.g. a Code Mode script gathering several add_code_cell calls)
    would otherwise each save their own copy and lose the other's change.
    """
    lock = _edit_locks.get(path)
    if lock is None:
        lock = asyncio.Lock()
        _edit_locks[path] = lock
    async with lock:
        nb = await load_notebook(path)
        yield nb
        await save_notebook(path, nb)
