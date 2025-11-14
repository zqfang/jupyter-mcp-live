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


async def get_file_id(file_path: str) -> str | None:
    """Get the file ID for a given file path from Jupyter's file manager.

    Args:
        file_path: Normalized path to file

    Returns:
        File ID string or None if not found
    """
    if not YDOC_AVAILABLE:
        return None

    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    if not serverapp:
        return None

    # Try to get file ID manager
    file_id_manager = getattr(serverapp, "file_id_manager", None)
    if not file_id_manager:
        logger.debug("No file_id_manager available")
        return None

    try:
        # Get file ID for the path
        file_id = file_id_manager.get_id(file_path)
        logger.debug(f"Got file_id {file_id} for {file_path}")
        return file_id
    except Exception as e:
        logger.debug(f"Failed to get file_id: {e}")
        return None


async def get_ydoc_room(room_id: str) -> Any | None:
    """Get YDoc room for a given room ID.

    Args:
        room_id: Room ID (format: "json:notebook:{file_id}")

    Returns:
        Room object or None if not found
    """
    if not YDOC_AVAILABLE:
        return None

    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    if not serverapp:
        return None

    # Try newer yroom_manager first
    yroom_manager = getattr(serverapp.web_app.settings, "yroom_manager", None)
    if yroom_manager:
        try:
            yroom = yroom_manager.get_room(room_id)
            if yroom:
                logger.debug(f"Found YDoc room via yroom_manager: {room_id}")
                return yroom
        except Exception as e:
            logger.debug(f"yroom_manager.get_room failed: {e}")

    # Fall back to older ywebsocket_server
    ywebsocket_server = getattr(serverapp.web_app.settings, "ywebsocket_server", None)
    if ywebsocket_server:
        try:
            rooms = getattr(ywebsocket_server, "rooms", {})
            yroom = rooms.get(room_id)
            if yroom:
                logger.debug(f"Found YDoc room via ywebsocket_server: {room_id}")
                return yroom
        except Exception as e:
            logger.debug(f"ywebsocket_server lookup failed: {e}")

    return None


async def get_ynotebook(file_path: str) -> Any | None:
    """Get YNotebook instance if notebook is open in JupyterLab.

    Args:
        file_path: Normalized path to notebook

    Returns:
        YNotebook instance or None if notebook is not open
    """
    if not YDOC_AVAILABLE:
        return None

    # Get file ID
    file_id = await get_file_id(file_path)
    if not file_id:
        return None

    # Get room
    room_id = f"json:notebook:{file_id}"
    yroom = await get_ydoc_room(room_id)
    if not yroom:
        return None

    # Get YNotebook from room
    try:
        # Try newer API
        if hasattr(yroom, "get_jupyter_ydoc"):
            ydoc = yroom.get_jupyter_ydoc()
        else:
            # Fall back to older API
            ydoc = getattr(yroom, "_document", None)

        if ydoc and isinstance(ydoc, YNotebook):
            logger.debug(f"YNotebook found for {file_path}")
            return ydoc

    except Exception as e:
        logger.debug(f"Failed to get YNotebook: {e}")

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
