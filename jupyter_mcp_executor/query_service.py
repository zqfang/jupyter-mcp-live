"""Query service for read-only notebook operations."""

import logging
from typing import Any

from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.notebook_config import QueryType
from jupyter_mcp_executor.notebook_models import CellIdentifier
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import get_server_context

logger = logging.getLogger(__name__)


class QueryService:
    """Service for read-only notebook queries.

    Handles:
    - Viewing cell source
    - Checking server status
    - Listing sessions
    - Finding cells by various identifiers
    """

    async def query(
        self,
        notebook_path: str,
        query_type: QueryType,
        execution_count: int | None = None,
        position_index: int | None = None,
        cell_id: str | None = None,
    ) -> dict[str, Any] | list | str | int:
        """Execute a query operation.

        Args:
            notebook_path: Path to notebook
            query_type: Type of query
            execution_count: Cell execution count (for some queries)
            position_index: Cell position (for some queries)
            cell_id: Cell UUID (for some queries)

        Returns:
            Query results (type depends on query_type)
        """
        if query_type == "view_source":
            return await self.view_source(
                notebook_path, execution_count, position_index
            )
        if query_type == "check_server":
            return self.check_server()
        if query_type == "list_sessions":
            return await self.list_sessions()
        if query_type == "list_kernels":
            return await KernelManager().list_notebook_kernels()
        if query_type == "get_position_index":
            return await self.get_position_index(
                notebook_path, execution_count, cell_id
            )
        msg = (
            f"Invalid query_type: {query_type}. "
            f"Must be one of: view_source, check_server, list_sessions, "
            f"list_kernels, get_position_index"
        )
        raise ValueError(msg)

    async def view_source(
        self,
        notebook_path: str,
        execution_count: int | None = None,
        position_index: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """View source code of notebook cells.

        Args:
            notebook_path: Path to notebook
            execution_count: View cell with this execution count
            position_index: View cell at this position

        Returns:
            Single cell dict or list of all cells
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # If no identifiers, return all cells
        if execution_count is None and position_index is None:
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

        # Find specific cell
        if execution_count is not None:
            result = await adapter.find_cell_by_execution_count(execution_count)
            if result is None:
                msg = f"No code cell with execution_count={execution_count} found"
                raise ValueError(msg)
            cell, position = result
        else:
            # position_index must be set
            cell = await adapter.read_cell(position_index)
            position = position_index

        # Return single cell
        cell_dict = {
            "cell_type": cell.cell_type,
            "source": cell.source,
            "metadata": cell.metadata,
            "execution_count": cell.execution_count,
            "outputs": cell.outputs,
            "id": cell.cell_id,
            "position": position,
        }

        # Filter outputs if it's a code cell
        if cell.cell_type == "code":
            cell_dict["outputs"] = OutputFilter.filter_outputs(cell.outputs)

        return cell_dict

    def check_server(self) -> str:
        """Check if Jupyter server is running and accessible.

        Returns:
            Status message
        """
        serverapp = get_server_context()
        if serverapp:
            return "Jupyter server is running and accessible"
        return "Jupyter server context not available"

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all notebook sessions on the server.

        Returns:
            List of session dicts
        """
        from jupyter_mcp_executor.server_context import (  # noqa: PLC0415
            require_server_context,
        )

        serverapp = require_server_context()
        session_manager = serverapp.session_manager

        sessions = []
        for session in await session_manager.list_sessions():
            sessions.append(dict(session))

        return sessions

    async def get_position_index(
        self,
        notebook_path: str,
        execution_count: int | None = None,
        cell_id: str | None = None,
    ) -> int:
        """Get the position index of a cell.

        Args:
            notebook_path: Path to notebook
            execution_count: Find cell with this execution count
            cell_id: Find cell with this ID

        Returns:
            Cell position index

        Raises:
            ValueError: If cell not found or invalid parameters
        """
        if execution_count is None and cell_id is None:
            msg = "Must provide either execution_count or cell_id"
            raise ValueError(msg)

        if execution_count is not None and cell_id is not None:
            msg = "Provide only ONE of execution_count or cell_id"
            raise ValueError(msg)

        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        if execution_count is not None:
            result = await adapter.find_cell_by_execution_count(execution_count)
            if result is None:
                msg = f"No code cell with execution_count={execution_count} found"
                raise ValueError(msg)
            _, position = result
            return position

        # cell_id is set
        result = await adapter.find_cell_by_id(cell_id)
        if result is None:
            msg = f"No cell with id={cell_id} found"
            raise ValueError(msg)
        _, position = result
        return position

    async def read_cell(
        self, notebook_path: str, identifier: CellIdentifier
    ) -> dict[str, Any]:
        """Read a single cell (convenience function).

        Args:
            notebook_path: Path to notebook
            identifier: Cell identifier

        Returns:
            Cell content dict
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

        # Build result dict
        cell_dict = {
            "cell_type": cell.cell_type,
            "source": cell.source,
            "metadata": cell.metadata,
            "execution_count": cell.execution_count,
            "outputs": cell.outputs,
            "id": cell.cell_id,
            "position": position,
        }

        # Filter outputs if it's a code cell
        if cell.cell_type == "code":
            cell_dict["outputs"] = OutputFilter.filter_outputs(cell.outputs)

        return cell_dict
