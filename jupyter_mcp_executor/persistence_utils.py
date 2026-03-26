"""Disk persistence utilities for YDoc collaborative mode.

When notebooks are open in JupyterLab, changes are made to YDoc (in-memory).
These utilities ensure structural changes are also persisted to disk.
"""

import logging
from typing import Any

from nbformat.v4 import new_code_cell, new_markdown_cell

from jupyter_mcp_executor.notebook_io import NotebookContext
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes

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
