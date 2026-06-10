"""Query service for read-only notebook operations."""

import logging
from typing import Any

from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.notebook_models import CellIdentifier, CellInfo
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import require_server_context

logger = logging.getLogger(__name__)


class QueryService:
    """Service for read-only notebook queries.

    Handles:
    - Viewing all notebook cells
    - Listing sessions
    - Reading single cells by various identifiers
    """

    async def view_notebook(self, notebook_path: str) -> list[dict[str, Any]]:
        """Get all cells of a notebook in order.

        Args:
            notebook_path: Path to notebook

        Returns:
            List of cell dicts (list index == cell position)
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        cells = await adapter.get_all_cells()
        return OutputFilter.filter_cells(
            [
                {
                    "cell_type": cell.cell_type,
                    "source": cell.source,
                    "metadata": cell.metadata,
                    "execution_count": cell.execution_count,
                    "outputs": cell.outputs,
                    "id": cell.cell_id,
                }
                for cell in cells
            ]
        )

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all notebook sessions on the server.

        Returns:
            List of session dicts
        """
        serverapp = require_server_context()
        session_manager = serverapp.session_manager

        sessions = []
        for session in await session_manager.list_sessions():
            sessions.append(dict(session))

        return sessions

    async def read_cell(
        self, notebook_path: str, identifier: CellIdentifier
    ) -> CellInfo:
        """Read a single cell (convenience function).

        Args:
            notebook_path: Path to notebook
            identifier: Cell identifier

        Returns:
            CellInfo Pydantic model with cell content
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Get position from identifier
        if identifier.position_index is not None:
            position = identifier.position_index
        elif identifier.cell_id is not None:
            result = await adapter.find_cell_by_id(identifier.cell_id)
            if result is None:
                msg = f"Cell with ID {identifier.cell_id} not found"
                raise ValueError(msg)
            _, position = result
        else:  # execution_count
            result = await adapter.find_cell_by_execution_count(
                identifier.execution_count
            )
            if result is None:
                msg = (
                    f"Cell with execution_count={identifier.execution_count} not found"
                )
                raise ValueError(msg)
            _, position = result

        # Read cell
        cell = await adapter.read_cell(position)

        # Get source as string
        source = (
            cell.get_source_as_string()
            if hasattr(cell, "get_source_as_string")
            else (cell.source if isinstance(cell.source, str) else "".join(cell.source))
        )

        # Filter outputs if it's a code cell
        filtered_outputs = (
            OutputFilter.filter_outputs(cell.outputs)
            if cell.cell_type == "code"
            else []
        )

        return CellInfo(
            cell_type=cell.cell_type,
            source=source,
            position=position,
            execution_count=cell.execution_count,
            outputs=filtered_outputs,
            id=cell.cell_id or "",
        )
