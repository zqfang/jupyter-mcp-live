"""Notebook execution tools for Jupyter Server MCP extension.

This module provides MCP tools for executing code and managing Jupyter kernels.
Supports collaborative editing via YDoc when notebooks are open in JupyterLab.

All implementation details are in submodules. This file only contains
the MCP-exported function interfaces that are registered as MCP tools.
"""

import json
from typing import Any

from fastmcp.tools.base import ToolResult
from jupyter_ai_tools.utils import collaborative_tool
from mcp.types import TextContent

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
    notebook_path: str,
    kernel_name: str | None = None,
    force_new_kernel: bool = False,
) -> dict[str, Any]:
    """Setup a notebook and start/reuse its kernel — CALL THIS FIRST, before executing code or modifying cells.

    Required before execute_cell / add_code_cell / edit_code_cell and the other
    cell-modification tools (they need a kernel). NOT required for the read-only
    tools view_notebook / read_cell — those work without a kernel or any setup.

    Creates an empty notebook if it doesn't exist; add content afterwards
    with add_code_cell / add_markdown_cell.

    By default, reuses the notebook's existing kernel (a running Jupyter
    session, e.g. one open in JupyterLab, or a kernel started earlier through
    these tools), preserving kernel state. When resuming work on a notebook
    from an earlier conversation, call this again with the same path — it
    reattaches to the still-running kernel and all variables remain available.
    Only set force_new_kernel=True if the user explicitly wants a clean state.

    Args:
        notebook_path: Path to the notebook (relative to the Jupyter root, or absolute inside it)
        kernel_name: Kernel spec name (default: the server's default kernel,
            normally "python3").
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
        - Kernels only start for notebooks Jupyter itself can open: inside the
          server's root directory and not in a hidden directory. The error
          says which; do not retry the same path.

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
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
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
    """Close a notebook's kernel session; its variables are lost.

    Shuts down the notebook's kernel and closes its Jupyter session — also
    when the notebook is open in JupyterLab, whose kernel this is.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        shutdown_kernel: If False, nothing is shut down: Jupyter cannot close
            a session without stopping its kernel, so the kernel and session
            keep running (default: True)

    Returns:
        dict with:
            - message: What happened
            - kernel_id: ID of the notebook's kernel (if it had one)
    """
    result = await container.kernel_manager.shutdown_notebook(
        notebook_path, shutdown_kernel
    )
    return result.model_dump(exclude_none=True)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def interrupt_notebook_kernel(notebook_path: str) -> dict[str, Any]:
    """Interrupt the cell currently running in a notebook's kernel.

    Like JupyterLab's stop button: the running cell ends with
    KeyboardInterrupt and all variables are KEPT. Use it when a cell hangs or
    runs much longer than intended (including cells started from JupyterLab).
    If the kernel is still busy afterwards (e.g. stuck in C code), use
    restart_notebook_kernel.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)

    Returns:
        dict with kernel_id and message

    Raises:
        ValueError: If the notebook has no running kernel
    """
    result = await container.kernel_manager.interrupt_notebook_kernel(notebook_path)
    return result.model_dump()


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def restart_notebook_kernel(notebook_path: str) -> dict[str, Any]:
    """Restart a notebook's kernel, clearing ALL variables and imports.

    Keeps the same kernel (and its JupyterLab session), but its state is
    gone: re-run setup/import cells before continuing. Use it when
    interrupt_notebook_kernel did not stop a cell or the kernel is wedged.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)

    Returns:
        dict with kernel_id and message

    Raises:
        ValueError: If the notebook has no running kernel
    """
    result = await container.kernel_manager.restart_notebook_kernel(notebook_path)
    # The restarted kernel needs fresh channels; drop the cached client.
    container.execution_engine.forget_kernel_client(result.kernel_id)
    return result.model_dump()


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def execute_cell(
    notebook_path: str,
    position_index: int,
) -> dict[str, Any]:
    """Execute an existing code cell in a notebook.

    Runs on the notebook's existing kernel (reused automatically); a new kernel
    is started only if none is running. started_new_kernel=True in the result
    means prior kernel state (variables) was NOT preserved — if that's
    unexpected, the previous kernel died; re-run setup cells.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        position_index: Positional index of cell to execute (0-based)

    Returns:
        dict with execution_count, outputs, status, kernel_id, and started_new_kernel flag

    Raises:
        ValueError: If cell at position_index is not a code cell
        IndexError: If position_index is out of range
        RuntimeError: If kernel execution fails

    Recovery: If you get IndexError or the cell isn't what you expected, the
    notebook may have changed (cells shift after add/delete, and the user can
    edit concurrently in JupyterLab). Call view_notebook() to
    re-check positions before retrying — don't retry the same index blindly.
    """
    notebook_path = normalize_path(notebook_path)
    result = await container.execution_engine.execute_cell_by_position(
        notebook_path, position_index
    )
    # Convert ExecutionResult to filtered dict for MCP response
    return result.to_filtered_dict()


def _cell_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a cell-operation result model to an MCP response dict.

    ExecutionResult uses to_filtered_dict() for token efficiency;
    CellOperationResult uses plain model_dump.
    """
    if hasattr(result, "to_filtered_dict"):
        return result.to_filtered_dict()
    return result.model_dump(exclude_none=True)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def add_code_cell(
    notebook_path: str,
    source: str,
    position_index: int | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    """Add a code cell and execute it immediately by default.

    Default to execute=True unless the user requests otherwise or you have good
    reason not to execute immediately. Execution reuses the notebook's existing
    kernel automatically; a new kernel is started only if none is running
    (started_new_kernel=True in the result means prior variables were lost).

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        source: Code for the new cell
        position_index: Where to insert (0-indexed); appends at end if omitted
        execute: Whether to execute the cell after adding (default: True)

    Returns:
        dict: With execute=True: execution_count, outputs, status, kernel_id.
            With execute=False: message, position, cell_type.
    """
    result = await container.cell_operations.add_code_cell(
        notebook_path, source, position_index, execute
    )
    return _cell_result_to_dict(result)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def edit_code_cell(
    notebook_path: str,
    position_index: int,
    source: str,
    execute: bool = True,
) -> dict[str, Any]:
    """Replace a code cell's source and re-execute it immediately by default.

    Execution reuses the notebook's existing kernel automatically; a new kernel
    is started only if none is running.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        position_index: Which cell to edit (0-indexed)
        source: New code for the cell
        execute: Whether to execute the cell after editing (default: True)

    Returns:
        dict: With execute=True: execution_count, outputs, status, kernel_id.
            With execute=False: message, position, cell_type.

    Raises:
        ValueError: If the cell at position_index is not a code cell
        IndexError: If position_index is out of range

    Recovery: If you get IndexError or the edited cell isn't what you expected,
    the notebook may have changed (cells shift after add/delete, and the user
    can edit concurrently in JupyterLab). Call view_notebook() to
    re-check positions before retrying — don't retry the same index blindly.
    """
    result = await container.cell_operations.edit_code_cell(
        notebook_path, position_index, source, execute
    )
    return _cell_result_to_dict(result)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def add_markdown_cell(
    notebook_path: str,
    source: str,
    position_index: int | None = None,
) -> dict[str, Any]:
    """Add a markdown cell.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        source: Markdown content for the new cell
        position_index: Where to insert (0-indexed); appends at end if omitted

    Returns:
        dict with message, position, and cell_type
    """
    result = await container.cell_operations.add_markdown_cell(
        notebook_path, source, position_index
    )
    return _cell_result_to_dict(result)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def edit_markdown_cell(
    notebook_path: str,
    position_index: int,
    source: str,
) -> dict[str, Any]:
    """Replace a markdown cell's content.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        position_index: Which cell to edit (0-indexed)
        source: New markdown content

    Returns:
        dict with message, position, and cell_type

    Raises:
        ValueError: If the cell at position_index is not a markdown cell
        IndexError: If position_index is out of range

    Recovery: If you get IndexError or the edited cell isn't what you expected,
    the notebook may have changed — call view_notebook() to re-check
    positions before retrying.
    """
    result = await container.cell_operations.edit_markdown_cell(
        notebook_path, position_index, source
    )
    return _cell_result_to_dict(result)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def delete_cell(
    notebook_path: str,
    position_index: int,
) -> dict[str, Any]:
    """Delete a cell; all later cells shift down by one position.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        position_index: Which cell to delete (0-indexed)

    Returns:
        dict with message

    Raises:
        IndexError: If position_index is out of range

    Recovery: If you get IndexError, the notebook may have changed — call
    view_notebook() to re-check positions before retrying.
    """
    result = await container.cell_operations.delete_cell(notebook_path, position_index)
    return _cell_result_to_dict(result)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def view_notebook(notebook_path: str) -> list[dict[str, Any]]:
    """View all cells of a notebook in order (read-only).

    Read-only: needs no kernel and no setup_notebook call. Prefer this over
    reading the .ipynb file directly (cat/open/nbformat) — when the notebook is
    open in JupyterLab it reflects the live, unsaved cell state and outputs,
    which a file read does not.

    Use this to see the notebook's current structure — e.g., to find cell
    positions before editing, or after add/delete operations shifted them.
    For a single cell, use read_cell instead. Not for listing kernels/sessions
    (use list_notebook_kernels / list_notebook_sessions).

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)

    Returns:
        list[dict]: One dict per cell, in notebook order, each containing
            cell_type, source, metadata, execution_count, outputs (filtered
            for token efficiency), and id. List index == position_index.
    """
    return await container.query_service.view_notebook(notebook_path)


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def read_cell(
    notebook_path: str,
    position_index: int | None = None,
    cell_id: str | None = None,
    execution_count: int | None = None,
) -> dict[str, Any]:
    """Read a single notebook cell - convenience function.

    Read-only: needs no kernel and no setup_notebook call. Prefer this over
    reading the .ipynb file directly — it reflects live unsaved JupyterLab
    state when the notebook is open.

    Single-cell complement to view_notebook; the returned dict includes the
    cell's position.
    Supports multiple ways to identify cells: position index, cell UUID, or execution count.

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
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


# Apply collaborative awareness
@collaborative_tool(user=user_info)
async def view_cell_image(
    notebook_path: str,
    position_index: int | None = None,
    cell_id: str | None = None,
    execution_count: int | None = None,
) -> ToolResult:
    """Look at a cell's images as real images: plots or embedded pictures.

    Code cell: its image outputs, which other tools show only as
    "[Filtered image/png ...]" placeholders — call this to check a plot.
    Markdown cell: images it embeds (pasted attachments, data: URIs); remote
    URLs and local image files are listed in skipped, not shown.
    Read-only: needs no kernel and no setup_notebook call.

    The images are attached to the tool response for you to view — also when
    called inside an execute script, so the script need not return them.
    PNG/JPEG/GIF/WEBP only, at most 4 per call; SVG cannot be viewed (re-plot
    as PNG).

    Args:
        notebook_path: Path to the notebook file (relative to the Jupyter root, or absolute inside it)
        position_index: Positional index of cell (0-indexed)
        cell_id: Cell UUID
        execution_count: The cell's [n] execution label (not its position)

    Returns:
        dict: position, cell_id, cell_type, images (one entry per attached
            image: source "output"/"attachment"/"data_uri", mime_type, bytes)
            and skipped (images not attached, with reason). Empty images: the
            cell has none to view.

    Note: Provide exactly ONE identifier (position_index, cell_id, or execution_count)
    """
    identifier = CellIdentifier(
        execution_count=execution_count, position_index=position_index, cell_id=cell_id
    )

    cell_images = await container.query_service.view_cell_images(
        notebook_path, identifier
    )
    summary = cell_images.summary()
    text = json.dumps(summary)
    if not cell_images.images:
        text = f"Cell {cell_images.position} has no viewable images. {text}"
    return ToolResult(
        content=[TextContent(type="text", text=text), *cell_images.images],
        structured_content=summary,
    )


# Export context management functions (used by extension.py)
__all__ = [
    "add_code_cell",
    "add_markdown_cell",
    "delete_cell",
    "edit_code_cell",
    "edit_markdown_cell",
    "execute_cell",
    "get_server_context",
    "interrupt_notebook_kernel",
    "list_notebook_kernels",
    "list_notebook_sessions",
    "read_cell",
    "restart_notebook_kernel",
    "set_server_context",
    "setup_notebook",
    "shutdown_notebook",
    "switch_notebook_kernel",
    "view_cell_image",
    "view_notebook",
]
