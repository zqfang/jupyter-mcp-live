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


async def get_ynotebook(file_path: str) -> Any | None:
    """Get YNotebook instance if notebook is open in JupyterLab.

    Uses the official YDocExtension.get_document() API from jupyter_server_ydoc.
    The ywebsocket_server is stored on the YDocExtension instance, NOT in
    web_app.settings, so it must be accessed via serverapp.extension_manager.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        Live YNotebook instance (copy=False) or None if notebook is not open
    """
    if not YDOC_AVAILABLE:
        return None

    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    if not serverapp:
        return None

    try:
        extension_manager = getattr(serverapp, "extension_manager", None)
        if not extension_manager:
            logger.debug("No extension_manager on serverapp")
            return None

        # extension_apps is {name: set_of_ExtensionApp_instances}
        ydoc_apps = extension_manager.extension_apps.get("jupyter_server_ydoc", set())
        if not ydoc_apps:
            logger.debug("jupyter_server_ydoc extension not loaded")
            return None

        ydoc_extension = next(iter(ydoc_apps))

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
            logger.debug(f"get_document returned unexpected type {type(ydoc)} for {file_path}")

    except Exception as e:
        logger.debug(f"Failed to get YNotebook via YDocExtension.get_document: {e}")

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
