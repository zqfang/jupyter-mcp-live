"""Notebook execution tools for Jupyter Server MCP extension.

This module provides MCP tools for executing code and managing Jupyter kernels.
Supports collaborative editing via YDoc when notebooks are open in JupyterLab.

All implementation details are in submodules. This file only contains
the MCP-exported function interfaces that are registered as MCP tools.
"""

from typing import Any

from jupyter_ai_tools.utils import collaborative_tool

from jupyter_mcp_executor.notebook_config import CellOperation, QueryType
from jupyter_mcp_executor.notebook_models import CellIdentifier
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import get_server_context, set_server_context
from jupyter_mcp_executor.service_container import container

# ============================================================================
# MCP-Exported Functions (Public API)
# ============================================================================
# Define user information
user_info = {
    "name": "Jupyter AI User",
    "color": "var(--jp-collaborator-color1)",
    "display_name": "Jupyter AI User",
}


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def list_notebook_kernels() -> dict[str, Any]:
    """List all available kernel specifications.

    Returns:
        Dict with "kernelspecs" key containing kernel spec dicts
    """
    return await container.kernel_manager.list_notebook_kernels()


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def list_notebook_sessions() -> list[dict[str, Any]]:
    """List all active notebook sessions with their kernels.

    Useful for seeing which notebooks are currently open and which kernels
    they're using (e.g., to debug kernel reuse).

    Returns:
        List of session dicts, each containing:
        - id: Session ID
        - path: Notebook path
        - name: Notebook filename
        - type: Session type (usually "notebook")
        - kernel: Dict with kernel info (id, name, execution_state, etc.)
    """
    return await container.query_service.list_sessions()


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def setup_notebook(
    notebook_path: str, kernel_name: str = "python3", force_new_kernel: bool = False
) -> dict[str, Any]:
    """Setup a notebook and start/reuse its kernel — CALL THIS FIRST, before any other notebook tool.

    Creates an empty notebook if it doesn't exist; add content afterwards
    with modify_notebook_cells.

    By default, reuses the notebook's existing kernel session (e.g., one already
    open in JupyterLab), preserving kernel state. Set force_new_kernel=True for
    a fresh kernel.

    Args:
        notebook_path: Path to the notebook (relative or absolute)
        kernel_name: Kernel spec name (default: "python3").
            Examples: "python3", "ir" (R), "julia-1.10". For R, "r"/"ir"/"irkernel"
            all auto-detect the available R kernel. Ignored when reusing an
            existing kernel.
        force_new_kernel: Always start a new kernel even if one exists (default: False)

    Agent guidance:
        - Honor an explicitly provided kernel_name from the user.
        - If the user names a language (R/Julia) rather than a kernel, call
          list_notebook_kernels first and pick the closest matching kernelspec key.
        - If setup fails due to an unknown kernel, surface the list from
          list_notebook_kernels in your error message.

    Returns:
        dict with:
            - path: Normalized notebook path
            - exists: Whether notebook existed before this call
            - kernel_name: Name of the kernel being used
            - kernel_id: ID of the kernel
    """
    result = await container.kernel_manager.setup_notebook(
        notebook_path, kernel_name, force_new_kernel
    )
    return result.model_dump(exclude_none=True)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def switch_notebook_kernel(
    notebook_path: str, kernel_name: str
) -> dict[str, Any]:
    """Switch notebook to use a different kernel.

    Updates the notebook's kernelspec metadata, shuts down the previous kernel,
    and starts a new kernel session. Kernel state from the previous kernel is lost.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        kernel_name: Kernel to switch to (e.g., "python3", "ir", "julia-1.10")

    Returns:
        dict with:
            - kernel_name: Name of the new kernel
            - kernel_id: ID of the new kernel
            - previous_kernel_id: ID of the previous kernel that was shut down
    """
    result = await container.kernel_manager.switch_kernel(notebook_path, kernel_name)
    return result.model_dump(exclude_none=True)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def shutdown_notebook(
    notebook_path: str, shutdown_kernel: bool = True
) -> dict[str, Any]:
    """Close a notebook session and clean local kernel state.

    Shuts down the kernel associated with a notebook and removes the
    notebook from active kernel tracking.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        shutdown_kernel: If True, shutdown the kernel (default: True)

    Returns:
        dict with:
            - message: Status message
            - kernel_id: ID of the kernel that was shut down (if any)
    """
    result = await container.kernel_manager.shutdown_notebook(
        notebook_path, shutdown_kernel
    )
    return result.model_dump(exclude_none=True)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def execute_notebook_code(
    notebook_path: str,
    position_index: int,
) -> dict[str, Any]:
    """Execute an existing code cell in a notebook.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        position_index: Positional index of cell to execute (0-based)

    Returns:
        dict with execution_count, outputs, status, kernel_id, and started_new_kernel flag

    Raises:
        ValueError: If cell at position_index is not a code cell
        IndexError: If position_index is out of range
        RuntimeError: If kernel execution fails

    Recovery: If you get IndexError or the cell isn't what you expected, the
    notebook may have changed (cells shift after add/delete, and the user can
    edit concurrently in JupyterLab). Call query_notebook(view_source) to
    re-check positions before retrying — don't retry the same index blindly.
    """
    notebook_path = normalize_path(notebook_path)
    result = await container.execution_engine.execute_cell_by_position(
        notebook_path, position_index
    )
    # Convert ExecutionResult to filtered dict for MCP response
    return result.to_filtered_dict()


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def modify_notebook_cells(
    notebook_path: str,
    operation: CellOperation,
    cell_content: str | None = None,
    position_index: int | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    """Modify notebook cells (add, edit, delete).

    Default to execute=True unless the user requests otherwise or you have good
    reason not to execute immediately.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        operation: Type of cell operation. Options:
            - 'add_code': Add (and optionally execute) a code cell at end or specific position
            - 'edit_code': Edit a code cell at specific position
            - 'add_markdown': Add a markdown cell at end or specific position
            - 'edit_markdown': Edit an existing markdown cell at specific position
            - 'delete': Delete a cell at specific position
        cell_content: Content for the cell (required for all operations except delete)
        position_index: Position index (0-indexed cell location). Must be an integer.
            - Optional for add_code/add_markdown: inserts there if provided, else appends at end
            - Required for edit_code/edit_markdown/delete: which cell to modify
        execute: Whether to execute code cells after adding/editing (default: True)

    Returns:
        dict: For add_code/edit_code with execute=True: execution_count, outputs, status.
            Otherwise: message (and error if the operation failed).

    Raises:
        ValueError: If invalid operation or missing required parameters
        IndexError: If position_index is out of range

    Recovery: If you get IndexError or the edited cell isn't what you expected,
    the notebook may have changed (cells shift after add/delete, and the user
    can edit concurrently in JupyterLab). Call query_notebook(view_source) to
    re-check positions before retrying — don't retry the same index blindly.
    """
    notebook_path = normalize_path(notebook_path)

    if operation == "add_code":
        if cell_content is None:
            msg = "cell_content required for add_code"
            raise ValueError(msg)
        result = await container.cell_operations.add_code_cell(
            notebook_path, cell_content, position_index, execute
        )
        # Convert Pydantic model to dict
        # ExecutionResult uses to_filtered_dict() for token efficiency
        if hasattr(result, "to_filtered_dict"):
            return result.to_filtered_dict()
        return result.model_dump(exclude_none=True)

    if operation == "edit_code":
        if cell_content is None or position_index is None:
            msg = "cell_content and position_index required for edit_code"
            raise ValueError(msg)
        result = await container.cell_operations.edit_code_cell(
            notebook_path, position_index, cell_content, execute
        )
        # Convert Pydantic model to dict
        # ExecutionResult uses to_filtered_dict() for token efficiency
        if hasattr(result, "to_filtered_dict"):
            return result.to_filtered_dict()
        return result.model_dump(exclude_none=True)

    if operation == "add_markdown":
        if cell_content is None:
            msg = "cell_content required for add_markdown"
            raise ValueError(msg)
        result = await container.cell_operations.add_markdown_cell(
            notebook_path, cell_content, position_index
        )
        return result.model_dump(exclude_none=True)

    if operation == "edit_markdown":
        if cell_content is None or position_index is None:
            msg = "cell_content and position_index required for edit_markdown"
            raise ValueError(msg)
        result = await container.cell_operations.edit_markdown_cell(
            notebook_path, position_index, cell_content
        )
        return result.model_dump(exclude_none=True)

    if operation == "delete":
        if position_index is None:
            msg = "position_index required for delete"
            raise ValueError(msg)
        result = await container.cell_operations.delete_cell(
            notebook_path, position_index
        )
        return result.model_dump(exclude_none=True)

    msg = f"Invalid operation: {operation}"
    raise ValueError(msg)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def query_notebook(
    notebook_path: str | None = None,
    query_type: QueryType = "view_source",
    execution_count: int | None = None,
    position_index: int | None = None,
    cell_id: str | None = None,
) -> dict[str, Any] | list | str | int:
    """Read notebook cells and positions (read-only); not for listing kernels/sessions.

    To list kernels or sessions, use list_notebook_kernels / list_notebook_sessions
    instead — those are NOT available here.

    Args:
        notebook_path: Path to the notebook file (relative or absolute).
            Required for view_source and get_position_index; ignored for check_server.
        query_type: Type of query to perform. Options:
            - 'view_source': View source of one cell, or all cells if no identifier given
            - 'get_position_index': Get the position index of a cell
            - 'check_server': Check if Jupyter server is running (no notebook_path needed)
        execution_count: (view_source/get_position_index) The number shown in square
            brackets like [3] in Jupyter UI. Only set for executed code cells.
            COMMON MISTAKE: Don't confuse with position_index!
            - execution_count=3 finds the cell that was executed 3rd (shows [3] in Jupyter)
            - position_index=3 finds the 4th cell in the notebook (0-indexed position)
        position_index: (view_source) Physical position of the cell, 0-indexed.
            Works for all cell types.
        cell_id: (get_position_index) Cell UUID like "205658d6-093c-...".

    Returns:
        - view_source: dict (single cell) or list[dict] (all cells)
        - get_position_index: int
        - check_server: str status message

    Examples:
        # View all cells
        await query_notebook("my_notebook.ipynb", "view_source")

        # View cell by execution count (the [3] shown in Jupyter UI)
        await query_notebook("my_notebook.ipynb", "view_source", execution_count=3)

        # Get position index of cell with execution count [5]
        await query_notebook("my_notebook.ipynb", "get_position_index", execution_count=5)

    Raises:
        ValueError: If invalid query_type or missing required parameters
    """
    return await container.query_service.query(
        notebook_path,
        query_type,
        execution_count=execution_count,
        position_index=position_index,
        cell_id=cell_id,
    )


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def read_cell(
    notebook_path: str,
    position_index: int | None = None,
    cell_id: str | None = None,
    execution_count: int | None = None,
) -> dict[str, Any]:
    """Read a single notebook cell - convenience function.

    Simpler alternative to query_notebook for reading individual cells.
    Supports multiple ways to identify cells: position index, cell UUID, or execution count.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        position_index: Positional index of cell (0-indexed)
        cell_id: Cell UUID
        execution_count: Execution count of code cell

    Returns:
        dict: Cell information containing:
            - cell_type: "code" or "markdown"
            - source: Cell source code or markdown
            - execution_count: Execution count (code cells only)
            - outputs: Cell outputs (code cells only, filtered for token efficiency)
            - id: Cell UUID
            - position: Cell position in notebook

    Note: Provide exactly ONE identifier (position_index, cell_id, or execution_count)

    Example:
        >>> cell = await read_cell("demo.ipynb", position_index=0)
        >>> print(cell["source"])
        "print('Hello World')"
    """
    identifier = CellIdentifier(
        execution_count=execution_count, position_index=position_index, cell_id=cell_id
    )

    result = await container.query_service.read_cell(notebook_path, identifier)
    return result.model_dump(exclude_none=True)


# Export context management functions (used by extension.py)
__all__ = [
    "execute_notebook_code",
    "get_server_context",
    "list_notebook_kernels",
    "list_notebook_sessions",
    "modify_notebook_cells",
    "query_notebook",
    "read_cell",
    "set_server_context",
    "setup_notebook",
    "shutdown_notebook",
    "switch_notebook_kernel",
]
