"""YDoc-based adapter for collaborative notebook editing."""

import logging
import uuid
from typing import Any

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.ydoc_helpers import (
    YDOC_AVAILABLE,
    YText,
    get_cell_index_by_id_ydoc,
)

logger = logging.getLogger(__name__)


class YDocAdapter(NotebookAdapter):
    """Adapter for notebooks open in JupyterLab (collaborative editing via YDoc).

    This adapter modifies the in-memory YDoc representation, which provides
    real-time collaboration and immediate visibility in JupyterLab.
    """

    def __init__(self, ydoc: Any, notebook_path: str):
        """Initialize with YNotebook instance.

        Args:
            ydoc: YNotebook instance from jupyter_ydoc
            notebook_path: Normalized path to notebook
        """
        if not YDOC_AVAILABLE:
            msg = "YDoc is not available (jupyter_ydoc not installed)"
            raise RuntimeError(msg)

        self.ydoc = ydoc
        self._notebook_path = notebook_path

    @property
    def is_collaborative(self) -> bool:
        """YDoc adapter supports real-time collaboration."""
        return True

    @property
    def notebook_path(self) -> str:
        """Path to the notebook file."""
        return self._notebook_path

    async def read_cell(self, position: int) -> CellContent:
        """Read cell from YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        return CellContent(
            source=str(ycell.get("source", "")),
            cell_type=str(ycell.get("cell_type", "code")),
            metadata=dict(ycell.get("metadata", {})),
            execution_count=ycell.get("execution_count"),
            outputs=list(ycell.get("outputs", [])),
            cell_id=str(ycell.get("id", "")),
        )

    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add cell to YDoc."""
        # Convert source to string
        source_str = cell_content.get_source_as_string()

        # Generate a single cell ID used for both YDoc and disk persistence.
        # This prevents duplication when JupyterLab syncs disk → YDoc on file
        # change: without a shared ID, YDoc and disk cells get different UUIDs
        # and the sync treats them as two distinct cells.
        cell_id = str(uuid.uuid4())

        # Convert CellContent to YDoc cell dict format
        cell_dict = {
            "id": cell_id,
            "cell_type": cell_content.cell_type,
            "source": YText(source_str) if YText else source_str,
            "metadata": cell_content.metadata.copy(),
        }

        if cell_content.cell_type == "code":
            cell_dict["outputs"] = cell_content.outputs.copy()
            cell_dict["execution_count"] = cell_content.execution_count

        # Determine position
        if position is None:
            position = self.ydoc.cell_number

        # Validate position
        if position < 0 or position > self.ydoc.cell_number:
            position = self.ydoc.cell_number

        # Add cell
        self.ydoc.ycells.insert(position, cell_dict)
        logger.info(
            f"Added {cell_content.cell_type} cell via YDoc at position {position}"
        )

        return position, cell_id

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        # Update source using YText for collaborative editing
        source_str = content.get_source_as_string()
        ysource = ycell.get("source")

        if YText and isinstance(ysource, YText):
            # Clear and update YText (preserves collaboration)
            ysource.clear()
            ysource.extend(source_str)
        else:
            # Fall back to direct assignment
            ycell["source"] = YText(source_str) if YText else source_str

        # Update metadata
        if content.metadata:
            ycell["metadata"] = content.metadata

        logger.info(f"Edited {content.cell_type} cell via YDoc at position {position}")

    async def delete_cell(self, position: int) -> None:
        """Delete cell from YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        self.ydoc.ycells.pop(position)
        logger.info(f"Deleted cell via YDoc at position {position}")

    async def update_cell_outputs(
        self, position: int, execution_count: int | None, outputs: list[dict[str, Any]]
    ) -> None:
        """Update cell outputs in YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        if execution_count is not None:
            ycell["execution_count"] = execution_count

        if outputs is not None:
            ycell["outputs"] = outputs

        logger.debug(
            f"Updated cell {position} outputs in YDoc (execution_count={execution_count})"
        )

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from YDoc."""
        cells = []
        for i in range(self.ydoc.cell_number):
            cells.append(await self.read_cell(i))
        return cells

    async def get_cell_count(self) -> int:
        """Get cell count from YDoc."""
        return self.ydoc.cell_number

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in YDoc."""
        position = get_cell_index_by_id_ydoc(self.ydoc, cell_id)
        if position is None:
            return None

        cell = await self.read_cell(position)
        return (cell, position)

    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count in YDoc."""
        for i in range(self.ydoc.cell_number):
            ycell = self.ydoc.ycells[i]
            if (
                ycell.get("cell_type") == "code"
                and ycell.get("execution_count") == execution_count
            ):
                cell = await self.read_cell(i)
                return (cell, i)

        return None
