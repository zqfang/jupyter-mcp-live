"""Disk persistence utilities for YDoc collaborative mode.

When notebooks are open in JupyterLab, changes are made to YDoc (in-memory).
These utilities ensure structural changes are also persisted to disk.

For open (collaborative) notebooks the preferred path is
``save_notebook_via_contents_manager`` which routes the save through Jupyter
Server's ContentsManager API.  That path does NOT trigger JupyterLab's
file-watcher reload, so the notebook viewport stays in place after an LLM
inserts or edits a cell.

Direct ``nbformat.write()`` calls (used by the ``persist_*_to_disk`` helpers
below) are still available for closed notebooks where there is no live YDoc
session.
"""

import inspect
import logging
import os
from typing import Any

from nbformat.v4 import new_code_cell, new_markdown_cell

from jupyter_mcp_executor.notebook_io import NotebookContext, read_notebook
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.server_context import get_server_context

logger = logging.getLogger(__name__)


async def persist_cell_addition_to_disk(
    notebook_path: str,
    position: int,
    cell_content: CellContent,
    cell_id: str | None = None,
) -> None:
    """Persist newly added cell to disk (for collaborative mode).

    This ensures YDoc changes are also saved to the .ipynb file.

    Args:
        notebook_path: Path to notebook
        position: Position where cell was added
        cell_content: Content of the added cell
        cell_id: Cell UUID to use for the disk cell. Must match the ID
            assigned by YDoc to prevent duplication when JupyterLab syncs
            the file change back into YDoc (it matches cells by ID).
    """
    try:
        with NotebookContext(notebook_path) as nb:
            # Validate position
            if position < 0 or position > len(nb.cells):
                logger.warning(
                    f"Cell position {position} out of valid range [0, {len(nb.cells)}]"
                )
                return

            # Create cell based on type
            source_str = cell_content.get_source_as_string()

            if cell_content.cell_type == "code":
                cell = new_code_cell(source=source_str, metadata=cell_content.metadata)
                if cell_content.execution_count is not None:
                    cell.execution_count = cell_content.execution_count
                if cell_content.outputs:
                    cell.outputs = convert_outputs_to_notebook_nodes(
                        cell_content.outputs
                    )
            else:  # markdown
                cell = new_markdown_cell(
                    source=source_str, metadata=cell_content.metadata
                )

            # Use the same cell ID as YDoc to prevent duplication on sync
            if cell_id is not None:
                cell.id = cell_id

            # Insert at position
            nb.cells.insert(position, cell)
            logger.debug(
                f"Persisted {cell_content.cell_type} cell addition to disk at position {position}"
            )

    except Exception as e:
        logger.warning(f"Failed to persist cell addition to disk: {e}")


async def persist_cell_edit_to_disk(
    notebook_path: str,
    position: int,
    cell_content: CellContent,
) -> None:
    """Persist cell edit to disk (for collaborative mode).

    This ensures YDoc changes are also saved to the .ipynb file.

    Args:
        notebook_path: Path to notebook
        position: Position of edited cell
        cell_content: New content for the cell
    """
    try:
        with NotebookContext(notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                logger.warning(
                    f"Cell index {position} out of range in disk notebook (has {len(nb.cells)} cells)"
                )
                return

            cell = nb.cells[position]

            # Update source
            source_str = cell_content.get_source_as_string()
            cell.source = source_str

            # Update metadata
            if cell_content.metadata:
                cell.metadata = cell_content.metadata

            logger.debug(f"Persisted cell edit to disk at position {position}")

    except Exception as e:
        logger.warning(f"Failed to persist cell edit to disk: {e}")


async def persist_cell_deletion_to_disk(
    notebook_path: str,
    position: int,
) -> None:
    """Persist cell deletion to disk (for collaborative mode).

    This ensures YDoc changes are also saved to the .ipynb file.

    Args:
        notebook_path: Path to notebook
        position: Position of deleted cell
    """
    try:
        with NotebookContext(notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                logger.warning(
                    f"Cell index {position} out of range in disk notebook (has {len(nb.cells)} cells)"
                )
                return

            nb.cells.pop(position)
            logger.debug(f"Persisted cell deletion to disk at position {position}")

    except Exception as e:
        logger.warning(f"Failed to persist cell deletion to disk: {e}")


async def persist_cell_outputs_to_disk(
    notebook_path: str,
    position: int,
    execution_count: int | None,
    outputs: list[dict[str, Any]],
) -> None:
    """Persist cell outputs to disk (for collaborative mode).

    This ensures YDoc changes are also saved to the .ipynb file.

    Args:
        notebook_path: Path to notebook
        position: Cell position
        execution_count: Execution count
        outputs: Output list
    """
    try:
        with NotebookContext(notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                logger.warning(
                    f"Cell index {position} out of range in disk notebook (has {len(nb.cells)} cells)"
                )
                return

            cell = nb.cells[position]
            if execution_count is not None:
                cell.execution_count = execution_count
            if outputs:
                cell.outputs = convert_outputs_to_notebook_nodes(outputs)

            logger.debug(f"Persisted cell outputs to disk at position {position}")

    except Exception as e:
        logger.warning(f"Failed to persist outputs to disk: {e}")


async def save_via_contents_manager(notebook_path: str, nb: Any) -> None:
    """Save a notebook object via Jupyter Server's ContentsManager API.

    Routing saves through the ContentsManager avoids triggering JupyterLab's
    external-file-change detector, which is what causes the scroll-to-top
    when ``nbformat.write()`` is called directly.

    Args:
        notebook_path: Absolute path to the notebook file.
        nb: nbformat notebook object to save.
    """
    try:
        serverapp = get_server_context()
        if not serverapp:
            logger.debug("No server context; skipping contents manager save")
            return

        cm = serverapp.contents_manager
        root_dir = getattr(serverapp, "root_dir", "")
        relative_path = os.path.relpath(notebook_path, root_dir) if root_dir else notebook_path

        model = {"type": "notebook", "content": nb, "format": "json"}

        result = cm.save(model, relative_path)
        if inspect.isawaitable(result):
            await result

        logger.debug(f"Saved notebook via contents manager: {relative_path}")
    except Exception as e:
        logger.warning(f"Failed to save via contents manager: {e}")


async def save_notebook_via_contents_manager(notebook_path: str) -> None:
    """Read the current notebook from disk and re-save via ContentsManager.

    Called after YDoc cell mutations in collaborative mode.  Using the
    ContentsManager channel instead of a direct ``nbformat.write()`` call
    prevents JupyterLab from treating the save as an external file change,
    which is the root cause of the viewport scrolling back to the top.

    The YDoc in-memory state (the source of truth while the notebook is open)
    is eventually persisted by JupyterLab's own autosave; this call provides
    an additional safety-net write that does not disturb the UI.

    Args:
        notebook_path: Absolute path to the notebook file.
    """
    try:
        nb = read_notebook(notebook_path)
        await save_via_contents_manager(notebook_path, nb)
    except Exception as e:
        logger.warning(f"Failed to save notebook via contents manager: {e}")
