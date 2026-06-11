"""Abstract adapter protocol for notebook operations.

This module defines the adapter pattern that allows transparent switching
between YDoc (collaborative) and nbformat (file-based) implementations.
"""

from abc import ABC, abstractmethod
from typing import Any

from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook


class NotebookAdapter(ABC):
    """Abstract base class for notebook operations.

    Implementations:
    - YDocAdapter: For notebooks open in JupyterLab (collaborative editing)
    - NbformatAdapter: For notebooks not open (file-based editing)
    """

    @abstractmethod
    async def read_cell(self, position: int) -> CellContent:
        """Read a cell at the given position.

        Args:
            position: Cell index

        Returns:
            Cell content

        Raises:
            IndexError: If position is out of range
        """

    @abstractmethod
    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add a cell.

        Args:
            cell_content: Content for the new cell
            position: Position to insert (None = append at end)

        Returns:
            (position, cell_id) tuple where position is where cell was added
            and cell_id is the UUID assigned to the cell
        """

    @abstractmethod
    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit a cell at the given position.

        Args:
            position: Cell index
            content: New cell content

        Raises:
            IndexError: If position is out of range
        """

    @abstractmethod
    async def delete_cell(self, position: int) -> None:
        """Delete cell at the given position.

        Args:
            position: Cell index

        Raises:
            IndexError: If position is out of range
        """

    @abstractmethod
    async def update_cell_outputs(
        self,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]] | None,
    ) -> None:
        """Update cell execution results.

        Args:
            position: Cell index
            execution_count: Execution count to set (None = leave unchanged)
            outputs: Output list to set (None = leave unchanged)

        Raises:
            IndexError: If position is out of range
        """

    @abstractmethod
    async def append_cell_output(self, position: int, output: dict[str, Any]) -> None:
        """Append a single output to a cell's existing outputs.

        Used for live output streaming during execution: outputs arrive one
        iopub message at a time and are mirrored into the notebook as they
        come, instead of replacing the whole list at the end.

        Args:
            position: Cell index
            output: One nbformat-style output dict (stream/display_data/
                execute_result/error)

        Raises:
            IndexError: If position is out of range
        """

    async def clear_cell_outputs(self, position: int) -> None:
        """Clear a cell's outputs (e.g., at execution start).

        Args:
            position: Cell index

        Raises:
            IndexError: If position is out of range
        """
        await self.update_cell_outputs(position, None, [])

    @abstractmethod
    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells in the notebook.

        Returns:
            List of all cells
        """

    @abstractmethod
    async def get_cell_count(self) -> int:
        """Get total number of cells.

        Returns:
            Cell count
        """

    @abstractmethod
    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID.

        Args:
            cell_id: Cell UUID

        Returns:
            (cell, position) tuple or None if not found
        """

    @abstractmethod
    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count.

        Args:
            execution_count: Execution count to search for

        Returns:
            (cell, position) tuple or None if not found
        """

    @property
    @abstractmethod
    def is_collaborative(self) -> bool:
        """Whether this adapter supports real-time collaboration.

        Returns:
            True for YDoc, False for nbformat
        """

    @property
    @abstractmethod
    def notebook_path(self) -> str:
        """Path to the notebook file.

        Returns:
            Normalized notebook path
        """


async def get_notebook_adapter(notebook_path: str) -> NotebookAdapter:
    """Factory function to get the appropriate adapter for a notebook.

    Returns YDocAdapter if notebook is open in JupyterLab,
    otherwise returns NbformatAdapter.

    Args:
        notebook_path: Path to notebook file

    Returns:
        Appropriate adapter instance
    """
    from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter  # noqa: PLC0415
    from jupyter_mcp_executor.ydoc_adapter import YDocAdapter  # noqa: PLC0415

    notebook_path = normalize_path(notebook_path)

    # Try to get YDoc (collaborative mode), recreating the room if needed
    ydoc = await ensure_ynotebook(notebook_path)
    if ydoc:
        return YDocAdapter(ydoc, notebook_path)

    # Fall back to file-based mode
    return NbformatAdapter(notebook_path)
