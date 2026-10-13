"""File-based adapter using nbformat."""

import asyncio
import logging
from typing import Any

from nbformat.v4 import new_code_cell, new_markdown_cell

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_io import NotebookContext, read_notebook
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.ydoc_helpers import get_cell_index_by_id_nbformat

logger = logging.getLogger(__name__)


def _cell_to_content(cell: Any) -> CellContent:
    """Convert an nbformat cell node to CellContent."""
    return CellContent(
        source=cell.source,
        cell_type=cell.cell_type,
        metadata=dict(cell.metadata),
        execution_count=getattr(cell, "execution_count", None),
        outputs=list(getattr(cell, "outputs", [])),
        cell_id=cell.get("id"),
        attachments=dict(cell.get("attachments") or {}),
    )


def _check_position(nb: Any, position: int) -> None:
    """Raise IndexError if position is out of range."""
    if position < 0 or position >= len(nb.cells):
        msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
        raise IndexError(msg)


class NbformatAdapter(NotebookAdapter):
    """Adapter for notebooks not open in JupyterLab (file-based editing).

    This adapter reads and writes notebook files directly using nbformat.
    Changes are persisted to disk immediately.  All disk I/O runs in a
    worker thread (asyncio.to_thread) so large notebooks never block the
    Jupyter server's event loop.  Read-only operations never write the
    file back.
    """

    def __init__(self, notebook_path: str):
        """Initialize with notebook path.

        Args:
            notebook_path: Normalized path to notebook file
        """
        self._notebook_path = notebook_path

    @property
    def is_collaborative(self) -> bool:
        """Nbformat adapter does not support real-time collaboration."""
        return False

    @property
    def notebook_path(self) -> str:
        """Path to the notebook file."""
        return self._notebook_path

    async def _read(self) -> Any:
        """Read the notebook from disk without blocking the event loop."""
        return await asyncio.to_thread(read_notebook, self._notebook_path)

    async def read_cell(self, position: int) -> CellContent:
        """Read cell from notebook file (read-only, no write-back)."""
        nb = await self._read()
        _check_position(nb, position)
        return _cell_to_content(nb.cells[position])

    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add cell to notebook file."""

        def _sync() -> tuple[int, str]:
            with NotebookContext(self._notebook_path) as nb:
                # Determine position
                pos = len(nb.cells) if position is None else position

                # Validate position
                if pos < 0 or pos > len(nb.cells):
                    pos = len(nb.cells)

                # Create cell
                source_str = cell_content.get_source_as_string()

                if cell_content.cell_type == "code":
                    cell = new_code_cell(
                        source=source_str, metadata=cell_content.metadata
                    )
                    if cell_content.execution_count is not None:
                        cell.execution_count = cell_content.execution_count
                    if cell_content.outputs:
                        cell.outputs = convert_outputs_to_notebook_nodes(
                            cell_content.outputs
                        )
                else:
                    cell = new_markdown_cell(
                        source=source_str, metadata=cell_content.metadata
                    )

                # Insert cell
                nb.cells.insert(pos, cell)
                logger.info(
                    f"Added {cell_content.cell_type} cell via nbformat at position {pos}"
                )
            return pos, cell.id

        return await asyncio.to_thread(_sync)

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in notebook file.

        For code cells the previous outputs and execution_count are replaced
        by the ones in `content` (typically empty), so stale outputs from the
        old source never linger on the edited cell.
        """

        def _sync() -> None:
            with NotebookContext(self._notebook_path) as nb:
                _check_position(nb, position)

                cell = nb.cells[position]
                cell.source = content.get_source_as_string()

                if content.metadata:
                    cell.metadata = content.metadata

                if cell.cell_type == "code":
                    cell.execution_count = content.execution_count
                    cell.outputs = convert_outputs_to_notebook_nodes(
                        content.outputs or []
                    )

                logger.info(
                    f"Edited {content.cell_type} cell via nbformat at position {position}"
                )

        await asyncio.to_thread(_sync)

    async def delete_cell(self, position: int) -> None:
        """Delete cell from notebook file."""

        def _sync() -> None:
            with NotebookContext(self._notebook_path) as nb:
                _check_position(nb, position)
                nb.cells.pop(position)
                logger.info(f"Deleted cell via nbformat at position {position}")

        await asyncio.to_thread(_sync)

    async def update_cell_outputs(
        self,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]] | None,
    ) -> None:
        """Update cell outputs in notebook file."""

        def _sync() -> None:
            with NotebookContext(self._notebook_path) as nb:
                _check_position(nb, position)

                cell = nb.cells[position]

                if execution_count is not None:
                    cell.execution_count = execution_count

                if outputs is not None:
                    cell.outputs = convert_outputs_to_notebook_nodes(outputs)

                logger.debug(f"Updated cell {position} outputs via nbformat")

        await asyncio.to_thread(_sync)

    async def append_cell_output(self, position: int, output: dict[str, Any]) -> None:
        """Append a single output to a cell in the notebook file.

        Closed notebooks have no live viewer, so the engine does not stream
        through this path; it exists for protocol completeness.
        """

        def _sync() -> None:
            with NotebookContext(self._notebook_path) as nb:
                _check_position(nb, position)

                cell = nb.cells[position]
                existing = list(getattr(cell, "outputs", []) or [])
                cell.outputs = existing + convert_outputs_to_notebook_nodes([output])

        await asyncio.to_thread(_sync)

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from notebook file (read-only, no write-back)."""
        nb = await self._read()
        return [_cell_to_content(cell) for cell in nb.cells]

    async def get_cell_count(self) -> int:
        """Get cell count from notebook file (read-only, no write-back)."""
        nb = await self._read()
        return len(nb.cells)

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in notebook file (read-only, no write-back)."""
        nb = await self._read()
        position = get_cell_index_by_id_nbformat(nb, cell_id)
        if position is None:
            return None
        return _cell_to_content(nb.cells[position]), position

    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count (read-only, no write-back)."""
        nb = await self._read()
        for i, cell in enumerate(nb.cells):
            if (
                cell.cell_type == "code"
                and getattr(cell, "execution_count", None) == execution_count
            ):
                return _cell_to_content(cell), i
        return None
