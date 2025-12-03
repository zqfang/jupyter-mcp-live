"""High-level cell operations service."""

import logging

from jupyter_mcp_executor.execution_engine import ExecutionEngine
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG
from jupyter_mcp_executor.notebook_models import (
    CellContent,
    CellOperationResult,
    ExecutionResult,
)
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.persistence_utils import (
    persist_cell_addition_to_disk,
    persist_cell_deletion_to_disk,
    persist_cell_edit_to_disk,
)

logger = logging.getLogger(__name__)


class CellOperations:
    """High-level service for cell manipulation operations.

    Handles:
    - Adding code and markdown cells
    - Editing cells
    - Deleting cells
    - Automatic execution for code cells
    - Persistence management for collaborative mode
    """

    def __init__(self, execution_engine: ExecutionEngine):
        """Initialize with execution engine for code cells.

        Args:
            execution_engine: Engine for executing code cells
        """
        self.execution_engine = execution_engine

    async def add_code_cell(
        self,
        notebook_path: str,
        code: str,
        position: int | None = None,
        execute: bool = True,
        timeout: int | None = None,
    ) -> ExecutionResult | CellOperationResult:
        """Add and optionally execute a code cell.

        Args:
            notebook_path: Path to notebook
            code: Code to add
            position: Position to insert (None = end)
            execute: Whether to execute the code
            timeout: Execution timeout in seconds

        Returns:
            ExecutionResult if execute=True, else CellOperationResult (both Pydantic models)
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Create cell content
        cell_content = CellContent(
            source=code, cell_type="code", execution_count=None, outputs=[]
        )

        # Add cell
        actual_position = await adapter.add_cell(cell_content, position)

        # If collaborative mode, persist structural change to disk
        if adapter.is_collaborative:
            logger.debug(
                f"Persisting cell addition at position {actual_position} to disk"
            )
            await persist_cell_addition_to_disk(
                notebook_path, actual_position, cell_content
            )

        # Execute if requested
        if execute:
            if timeout is None:
                timeout = EXECUTION_CONFIG.default_timeout

            # Get tracked kernel for this notebook
            kernel_id = self.execution_engine.kernel_tracker.get(notebook_path)

            return await self.execution_engine.execute_and_update_cell(
                adapter, actual_position, code, kernel_id=kernel_id, timeout=timeout
            )

        return CellOperationResult(
            message=f"Added code cell at position {actual_position}",
            position=actual_position,
            cell_type="code",
        )

    async def edit_code_cell(
        self,
        notebook_path: str,
        position: int,
        code: str,
        execute: bool = True,
        timeout: int | None = None,
    ) -> ExecutionResult | CellOperationResult:
        """Edit and optionally execute a code cell.

        Args:
            notebook_path: Path to notebook
            position: Cell position to edit
            code: New code content
            execute: Whether to execute the code
            timeout: Execution timeout in seconds

        Returns:
            ExecutionResult if execute=True, else CellOperationResult (both Pydantic models)
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Read existing cell
        existing_cell = await adapter.read_cell(position)
        if existing_cell.cell_type != "code":
            msg = f"Cell at position {position} is not a code cell"
            raise ValueError(msg)

        # Update content
        cell_content = CellContent(
            source=code,
            cell_type="code",
            metadata=existing_cell.metadata,
            execution_count=None,
            outputs=[],
        )

        # Edit cell
        await adapter.edit_cell(position, cell_content)

        # If collaborative mode, persist structural change to disk
        if adapter.is_collaborative:
            logger.debug(f"Persisting cell edit at position {position} to disk")
            await persist_cell_edit_to_disk(notebook_path, position, cell_content)

        # Execute if requested
        if execute:
            if timeout is None:
                timeout = EXECUTION_CONFIG.default_timeout

            # Get tracked kernel for this notebook
            kernel_id = self.execution_engine.kernel_tracker.get(notebook_path)

            return await self.execution_engine.execute_and_update_cell(
                adapter, position, code, kernel_id=kernel_id, timeout=timeout
            )

        return CellOperationResult(
            message=f"Edited code cell at position {position}",
            position=position,
            cell_type="code",
        )

    async def add_markdown_cell(
        self, notebook_path: str, markdown: str, position: int | None = None
    ) -> CellOperationResult:
        """Add a markdown cell.

        Args:
            notebook_path: Path to notebook
            markdown: Markdown content
            position: Position to insert (None = end)

        Returns:
            CellOperationResult with position and message
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Create cell content
        cell_content = CellContent(source=markdown, cell_type="markdown")

        # Add cell
        actual_position = await adapter.add_cell(cell_content, position)

        # If collaborative mode, persist structural change to disk
        if adapter.is_collaborative:
            logger.debug(
                f"Persisting markdown cell addition at position {actual_position} to disk"
            )
            await persist_cell_addition_to_disk(
                notebook_path, actual_position, cell_content
            )

        return CellOperationResult(
            message=f"Added markdown cell at position {actual_position}",
            position=actual_position,
            cell_type="markdown",
        )

    async def edit_markdown_cell(
        self, notebook_path: str, position: int, markdown: str
    ) -> CellOperationResult:
        """Edit a markdown cell.

        Args:
            notebook_path: Path to notebook
            position: Cell position to edit
            markdown: New markdown content

        Returns:
            CellOperationResult with position and message
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Read existing cell
        existing_cell = await adapter.read_cell(position)
        if existing_cell.cell_type != "markdown":
            msg = f"Cell at position {position} is not a markdown cell"
            raise ValueError(msg)

        # Update content
        cell_content = CellContent(
            source=markdown, cell_type="markdown", metadata=existing_cell.metadata
        )

        # Edit cell
        await adapter.edit_cell(position, cell_content)

        # If collaborative mode, persist structural change to disk
        if adapter.is_collaborative:
            logger.debug(
                f"Persisting markdown cell edit at position {position} to disk"
            )
            await persist_cell_edit_to_disk(notebook_path, position, cell_content)

        return CellOperationResult(
            message=f"Edited markdown cell at position {position}",
            position=position,
            cell_type="markdown",
        )

    async def delete_cell(
        self, notebook_path: str, position: int
    ) -> CellOperationResult:
        """Delete a cell.

        Args:
            notebook_path: Path to notebook
            position: Cell position to delete

        Returns:
            CellOperationResult with position and message
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Delete cell
        await adapter.delete_cell(position)

        # If collaborative mode, persist structural change to disk
        if adapter.is_collaborative:
            logger.debug(f"Persisting cell deletion at position {position} to disk")
            await persist_cell_deletion_to_disk(notebook_path, position)

        return CellOperationResult(
            message=f"Deleted cell at position {position}", position=position
        )
