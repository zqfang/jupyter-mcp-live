"""File-based adapter using nbformat."""

import logging
from typing import Any

from nbformat.v4 import new_code_cell, new_markdown_cell

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_io import NotebookContext
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.ydoc_helpers import get_cell_index_by_id_nbformat

logger = logging.getLogger(__name__)


class NbformatAdapter(NotebookAdapter):
    """Adapter for notebooks not open in JupyterLab (file-based editing).

    This adapter reads and writes notebook files directly using nbformat.
    Changes are persisted to disk immediately.
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

    async def read_cell(self, position: int) -> CellContent:
        """Read cell from notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
                raise IndexError(
                    msg
                )

            cell = nb.cells[position]

            return CellContent(
                source=cell.source,
                cell_type=cell.cell_type,
                metadata=dict(cell.metadata),
                execution_count=getattr(cell, "execution_count", None),
                outputs=list(getattr(cell, "outputs", [])),
                cell_id=cell.get("id")
            )

    async def add_cell(
        self,
        cell_content: CellContent,
        position: int | None = None
    ) -> int:
        """Add cell to notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            # Determine position
            if position is None:
                position = len(nb.cells)

            # Validate position
            if position < 0 or position > len(nb.cells):
                position = len(nb.cells)

            # Create cell
            source_str = cell_content.get_source_as_string()

            if cell_content.cell_type == "code":
                cell = new_code_cell(
                    source=source_str,
                    metadata=cell_content.metadata
                )
                if cell_content.execution_count is not None:
                    cell.execution_count = cell_content.execution_count
                if cell_content.outputs:
                    cell.outputs = convert_outputs_to_notebook_nodes(cell_content.outputs)
            else:
                cell = new_markdown_cell(
                    source=source_str,
                    metadata=cell_content.metadata
                )

            # Insert cell
            nb.cells.insert(position, cell)
            logger.info(f"Added {cell_content.cell_type} cell via nbformat at position {position}")

        return position

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
                raise IndexError(
                    msg
                )

            cell = nb.cells[position]
            cell.source = content.get_source_as_string()

            if content.metadata:
                cell.metadata = content.metadata

            logger.info(f"Edited {content.cell_type} cell via nbformat at position {position}")

    async def delete_cell(self, position: int) -> None:
        """Delete cell from notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
                raise IndexError(
                    msg
                )

            nb.cells.pop(position)
            logger.info(f"Deleted cell via nbformat at position {position}")

    async def update_cell_outputs(
        self,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]]
    ) -> None:
        """Update cell outputs in notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            if position < 0 or position >= len(nb.cells):
                msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
                raise IndexError(
                    msg
                )

            cell = nb.cells[position]

            if execution_count is not None:
                cell.execution_count = execution_count

            if outputs is not None:
                cell.outputs = convert_outputs_to_notebook_nodes(outputs)

            logger.debug(f"Updated cell {position} outputs via nbformat")

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            cells = []
            for cell in nb.cells:
                cells.append(CellContent(
                    source=cell.source,
                    cell_type=cell.cell_type,
                    metadata=dict(cell.metadata),
                    execution_count=getattr(cell, "execution_count", None),
                    outputs=list(getattr(cell, "outputs", [])),
                    cell_id=cell.get("id")
                ))
            return cells

    async def get_cell_count(self) -> int:
        """Get cell count from notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            return len(nb.cells)

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            position = get_cell_index_by_id_nbformat(nb, cell_id)
            if position is None:
                return None

        # Read cell outside context to avoid holding file open
        cell = await self.read_cell(position)
        return (cell, position)

    async def find_cell_by_execution_count(
        self,
        execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count in notebook file."""
        with NotebookContext(self._notebook_path) as nb:
            for i, cell in enumerate(nb.cells):
                if (cell.cell_type == "code" and
                    getattr(cell, "execution_count", None) == execution_count):
                    # Found it - record position
                    position = i
                    break
            else:
                return None

        # Read cell outside context
        cell = await self.read_cell(position)
        return (cell, position)
