"""Notebook execution tools for Jupyter Server MCP extension.

This module provides MCP tools for executing code and managing Jupyter kernels.
Supports collaborative editing via YDoc when notebooks are open in JupyterLab.

All implementation details are in submodules. This file only contains
the MCP-exported function interfaces that are registered as MCP tools.
"""

from typing import Any

# Import services
from jupyter_server_mcp.server_context import set_server_context, get_server_context
from jupyter_server_mcp.kernel_manager import KernelManager
from jupyter_server_mcp.cell_operations import CellOperations
from jupyter_server_mcp.execution_engine import ExecutionEngine
from jupyter_server_mcp.query_service import QueryService
from jupyter_server_mcp.path_utils import normalize_path
from jupyter_server_mcp.notebook_models import CellIdentifier
from jupyter_server_mcp.notebook_config import QueryType, ExecutionType, CellOperation

# Initialize services (singleton pattern)
_execution_engine = ExecutionEngine()
_cell_operations = CellOperations(_execution_engine)
_kernel_manager = KernelManager()
_query_service = QueryService()


# ============================================================================
# MCP-Exported Functions (Public API)
# ============================================================================

async def list_running_kernels() -> dict[str, Any]:
    """List all running Jupyter kernels.

    Returns:
        Dict with "kernels" key containing list of kernel info dicts
    """
    return await _kernel_manager.list_running_kernels()


async def list_available_kernels() -> dict[str, Any]:
    """List all available kernel specifications.

    Returns:
        Dict with "kernelspecs" key containing kernel spec dicts
    """
    return await _kernel_manager.list_available_kernels()


async def setup_notebook(
    notebook_path: str,
    kernel_name: str = "python3"
) -> dict[str, Any]:
    """Setup a notebook (create if needed) and start kernel.

    Will create a new empty Jupyter notebook if it doesn't exist.

    **CALL THIS FIRST** - This tool must be called before using other notebook tools
    to establish the server URL connection. All subsequent notebook operations will
    use the server URL stored by this tool.

    This tool creates an empty notebook. To add content, use the modify_notebook_cells
    tool after creation.

    Example usage:
        # Step 1: Setup notebook with Python kernel
        await setup_notebook("demo.ipynb")

        # Step 2: Add cells
        await modify_notebook_cells("demo.ipynb", "add_markdown", "# Title\\n\\nDescription")
        await modify_notebook_cells("demo.ipynb", "add_code", "print('Hello World')")

        # For R notebooks:
        await setup_notebook("demo_r.ipynb", kernel_name="ir")
        await modify_notebook_cells("demo_r.ipynb", "add_code", "library(ggplot2)")

    Args:
        notebook_path: Path to the notebook (relative or absolute)
        kernel_name: Name of the kernel to use (default: "python3")
                     - Examples: "python3", "python3.11", "ir" (R), "julia-1.10"
                     - For R kernels, you can use "r", "ir", or "irkernel"
                     - To discover available kernels, call list_available_kernels() first

    Agent guidance:
        - Always honor an explicitly provided `kernel_name` from the user.
        - If the user specifies a language (e.g., R/Julia) rather than a kernel name, map it by first calling
          `list_available_kernels` and selecting the closest matching kernelspec key.
        - For R kernels, you can use "r", "ir", or "irkernel" - the system will auto-detect the correct available kernel.
        - If setup fails due to an unknown kernel, surface the list from `list_available_kernels` in the error/help message.

    Returns:
        dict: Information about the notebook and status message.
    """
    return await _kernel_manager.setup_notebook(notebook_path, kernel_name)


async def switch_notebook_kernel(
    notebook_path: str,
    kernel_name: str
) -> dict[str, Any]:
    """Switch notebook to use a different kernel.

    This updates the notebook's kernelspec metadata and creates a new
    kernel session with the specified kernel.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        kernel_name: Name of the kernel to switch to (e.g., "python3", "ir", "julia-1.10")

    Returns:
        dict: Status information containing:
            - message: Status message
            - kernel_name: Name of the new kernel
            - kernel_id: ID of the new kernel

    Example:
        >>> # Switch from Python to R
        >>> result = await switch_notebook_kernel("analysis.ipynb", "ir")
        >>> print(result)
        {
            "message": "Kernel switched to ir",
            "kernel_name": "ir",
            "kernel_id": "xyz789"
        }
    """
    return await _kernel_manager.switch_kernel(notebook_path, kernel_name)


async def shutdown_notebook(
    notebook_path: str,
    shutdown_kernel: bool = True
) -> dict[str, Any]:
    """Close a notebook session and clean local kernel state.

    This shuts down the kernel associated with a notebook and removes
    the notebook from the active kernel tracking.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        shutdown_kernel: If True, shutdown the kernel (default: True)

    Returns:
        dict: Status information containing:
            - message: Status message
            - kernel_id: ID of the kernel that was shut down (if any)

    Example:
        >>> result = await shutdown_notebook("demo.ipynb")
        >>> print(result)
        {
            "message": "Notebook session closed",
            "kernel_id": "abc123"
        }
    """
    return await _kernel_manager.shutdown_notebook(notebook_path, shutdown_kernel)


async def execute_notebook_code(
    notebook_path: str,
    execution_type: ExecutionType,
    position_index: int | None = None,
    package_names: str | None = None
) -> dict[str, Any] | str:
    """Execute code in a notebook (execute cell or install packages).

    This consolidates all code execution operations into a single tool following MCP best practices.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        execution_type: Type of execution operation. Options:
            - 'execute_cell': Execute an existing code cell
            - 'install_packages': Install packages using pip in the notebook environment
        position_index: (For execute_cell) Positional index of cell to execute
        package_names: (For install_packages) Space-separated list of package names to install

    Returns:
        Union[dict, str]:
            - execute_cell: dict with execution_count, outputs, status
            - install_packages: str with installation result message

    Raises:
        ValueError: If invalid execution_type or missing required parameters
        IndexError: If position_index is out of range
        RuntimeError: If kernel execution fails
    """
    notebook_path = normalize_path(notebook_path)

    if execution_type == "execute_cell":
        if position_index is None:
            raise ValueError("position_index required for execute_cell")
        return await _execution_engine.execute_cell_by_position(
            notebook_path, position_index
        )
    elif execution_type == "install_packages":
        if not package_names:
            raise ValueError("package_names required for install_packages")
        return await _execution_engine.install_packages(
            notebook_path, package_names
        )
    else:
        raise ValueError(f"Invalid execution_type: {execution_type}")


async def modify_notebook_cells(
    notebook_path: str,
    operation: CellOperation,
    cell_content: str | None = None,
    position_index: int | None = None,
    execute: bool = True
) -> dict[str, Any]:
    """Modify notebook cells (add, edit, delete).

    This consolidates all cell modification operations into a single tool following MCP best practices.
    Default to execute=True unless the user requests otherwise or you have good reason not to
    execute immediately.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        operation: Type of cell operation. Options:
            - 'add_code': Add (and optionally execute) a code cell at end or specific position
            - 'edit_code': Edit a code cell at specific position
            - 'add_markdown': Add a markdown cell at end or specific position
            - 'edit_markdown': Edit an existing markdown cell at specific position
            - 'delete': Delete a cell at specific position
        cell_content: Content for the cell (required for add_code, edit_code, add_markdown, edit_markdown)
        position_index: Position index (0-indexed cell location) for operations. Must be an integer.
            - Optional for add_code/add_markdown: if provided, inserts at that position; if not, adds at end
            - Required for edit_code/edit_markdown/delete: specifies which cell to modify
            Examples: position_index=0 (first cell), position_index=2 (third cell)
        execute: Whether to execute code cells after adding/editing (default: True)

    Returns:
        dict: Operation results containing:
            - For add_code/edit_code with execute=True: execution_count, outputs, status
            - For add_code/edit_code with execute=False: message field
            - For add_markdown/edit_markdown: message and error fields
            - For delete: message and error fields

    Raises:
        ValueError: If invalid operation or missing required parameters
        IndexError: If position_index is out of range
    """
    notebook_path = normalize_path(notebook_path)

    if operation == "add_code":
        if cell_content is None:
            raise ValueError("cell_content required for add_code")
        return await _cell_operations.add_code_cell(
            notebook_path, cell_content, position_index, execute
        )

    elif operation == "edit_code":
        if cell_content is None or position_index is None:
            raise ValueError("cell_content and position_index required for edit_code")
        return await _cell_operations.edit_code_cell(
            notebook_path, position_index, cell_content, execute
        )

    elif operation == "add_markdown":
        if cell_content is None:
            raise ValueError("cell_content required for add_markdown")
        return await _cell_operations.add_markdown_cell(
            notebook_path, cell_content, position_index
        )

    elif operation == "edit_markdown":
        if cell_content is None or position_index is None:
            raise ValueError("cell_content and position_index required for edit_markdown")
        return await _cell_operations.edit_markdown_cell(
            notebook_path, position_index, cell_content
        )

    elif operation == "delete":
        if position_index is None:
            raise ValueError("position_index required for delete")
        return await _cell_operations.delete_cell(notebook_path, position_index)

    else:
        raise ValueError(f"Invalid operation: {operation}")


async def query_notebook(
    notebook_path: str,
    query_type: QueryType,
    execution_count: int | None = None,
    position_index: int | None = None,
    cell_id: str | None = None
) -> dict[str, Any] | list | str | int:
    """Query notebook information and metadata.

    This consolidates all read-only operations into a single tool following MCP best practices.

    Args:
        notebook_path: Path to the notebook file (relative or absolute)
        query_type: Type of query to perform. Options:
            - 'view_source': View source code of notebook (single cell or all cells)
            - 'check_server': Check if Jupyter server is running and accessible
            - 'list_sessions': List all notebook sessions on the server
            - 'list_kernels': List all available kernelspecs on the server
            - 'get_position_index': Get the index of a code cell
        execution_count: (For view_source/get_position_index) The execution count to look for.
            IMPORTANT: This is the number shown in square brackets like [3] in Jupyter UI.
            Only available for executed code cells. Must be an integer (e.g., 3).
            COMMON MISTAKE: Don't confuse with position_index!
            - execution_count=3 finds the cell that was executed 3rd (shows [3] in Jupyter)
            - position_index=3 finds the 4th cell in the notebook (0-indexed position)
        position_index: (For view_source) The position index to look for.
            This is the cell's physical position in the notebook (0-indexed).
            Examples: first cell = 0, second cell = 1, third cell = 2, etc.
            Works for all cell types (code, markdown, raw). Must be an integer.
        cell_id: (For get_position_index) Cell ID like "205658d6-093c-4722-854c-90b149f254ad".
            This is a unique identifier for each cell, visible in notebook metadata.

    Returns:
        Union[dict, list, str, int]:
            - view_source: dict (single cell) or list[dict] (all cells) with cell contents/metadata
            - check_server: str status message
            - list_sessions: list of notebook sessions
            - list_kernels: dict of available kernelspecs
            - get_position_index: int positional index

    Examples:
        # View all cells in notebook
        await query_notebook("my_notebook.ipynb", "view_source")

        # View cell by execution count (the [3] shown in Jupyter UI)
        await query_notebook("my_notebook.ipynb", "view_source", execution_count=3)

        # View cell by position (first cell=0, second=1, etc)
        await query_notebook("my_notebook.ipynb", "view_source", position_index=0)

        # Get position index of cell with execution count [5]
        await query_notebook("my_notebook.ipynb", "get_position_index", execution_count=5)

        # List available kernels
        await query_notebook("my_notebook.ipynb", "list_kernels")

    Raises:
        ValueError: If invalid query_type or missing required parameters
    """
    return await _query_service.query(
        notebook_path,
        query_type,
        execution_count=execution_count,
        position_index=position_index,
        cell_id=cell_id
    )


async def read_cell(
    notebook_path: str,
    position_index: int | None = None,
    cell_id: str | None = None,
    execution_count: int | None = None
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
            - metadata: Cell metadata
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
        execution_count=execution_count,
        position_index=position_index,
        cell_id=cell_id
    )

    return await _query_service.read_cell(notebook_path, identifier)


# Export context management functions (used by extension.py)
__all__ = [
    "set_server_context",
    "get_server_context",
    "list_running_kernels",
    "list_available_kernels",
    "setup_notebook",
    "switch_notebook_kernel",
    "shutdown_notebook",
    "execute_notebook_code",
    "modify_notebook_cells",
    "query_notebook",
    "read_cell",
]
