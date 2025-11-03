"""Notebook execution tools for Jupyter Server MCP extension.

This module provides tools for executing code and managing Jupyter kernels.
Supports collaborative editing via YDoc when notebooks are open in JupyterLab.
"""

import asyncio
import inspect
import logging
import os
from typing import Any
from urllib.parse import unquote

import nbformat
from nbformat.notebooknode import from_dict
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

# Collaborative editing support (optional)
try:
    from jupyter_ydoc import YNotebook
    from pycrdt import Text as YText
    YDOC_AVAILABLE = True
except ImportError:
    YDOC_AVAILABLE = False
    YNotebook = None
    YText = None

logger = logging.getLogger(__name__)

# Global context to store the Jupyter ServerApp instance
# This global variable will be set when the MCP extension is initialized
# see extension.py
_server_context = {"serverapp": None}


class _KernelTracker:
    """Track active kernels per notebook to support multi-kernel workflows."""
    
    def __init__(self):
        """Initialize kernel tracker."""
        self._kernels: dict[str, str] = {}  # Maps notebook_path -> kernel_id
    
    def track(self, notebook_path: str, kernel_id: str) -> None:
        """Track a kernel for a notebook.
        
        Args:
            notebook_path: Normalized path to notebook
            kernel_id: Kernel ID to track
        """
        if kernel_id:
            self._kernels[notebook_path] = kernel_id
            logger.debug(f"Tracking kernel {kernel_id} for {notebook_path}")
    
    def get(self, notebook_path: str) -> str | None:
        """Get the tracked kernel ID for a notebook.
        
        Args:
            notebook_path: Normalized path to notebook
            
        Returns:
            Kernel ID if tracked, None otherwise
        """
        return self._kernels.get(notebook_path)
    
    def untrack(self, notebook_path: str) -> None:
        """Stop tracking kernel for a notebook.
        
        Args:
            notebook_path: Normalized path to notebook
        """
        if notebook_path in self._kernels:
            kernel_id = self._kernels[notebook_path]
            del self._kernels[notebook_path]
            logger.debug(f"Untracked kernel {kernel_id} for {notebook_path}")


# Global kernel tracker instance
_kernel_tracker = _KernelTracker()


# ============================================================================
# Helper Functions
# ============================================================================

def _normalize_path(path: str) -> str:
    """Normalize a file path to an absolute path.

    Handles URL-encoded paths and resolves relative paths against
    the Jupyter server root directory if available.
    """
    if not path or not path.strip():
        raise ValueError("path cannot be empty")

    # URL decode in case path contains encoded characters
    decoded_path = unquote(path)

    # If already absolute, return normalized
    if os.path.isabs(decoded_path):
        return os.path.abspath(decoded_path)

    # For relative paths, try to resolve against server root
    serverapp = get_server_context()
    if serverapp and hasattr(serverapp, 'root_dir'):
        root_dir = serverapp.root_dir
        return os.path.abspath(os.path.join(root_dir, decoded_path))

    # Fallback to current working directory
    return os.path.abspath(decoded_path)


def _require_server_context():
    """Get server context or raise exception.
    
    Call this once per operation instead of checking repeatedly.
    
    Returns:
        The Jupyter ServerApp instance
        
    Raises:
        RuntimeError: If server context is not available
    """
    serverapp = get_server_context()
    if serverapp is None:
        raise RuntimeError("Server context not available")
    return serverapp


def _validate_cell_index(nb, index: int, operation: str = "access") -> None:
    """Validate cell index or raise descriptive error.
    
    Args:
        nb: Notebook object
        index: Cell index to validate
        operation: Operation being performed (for error message)
        
    Raises:
        IndexError: If index is out of range
    """
    if index >= len(nb.cells):
        raise IndexError(
            f"Cannot {operation} cell at index {index}: "
            f"notebook has {len(nb.cells)} cells (valid indices: 0-{len(nb.cells)-1})"
        )


def _validate_code_cell(cell, index: int) -> None:
    """Validate cell is a code cell.
    
    Args:
        cell: Cell object
        index: Cell index (for error message)
        
    Raises:
        ValueError: If cell is not a code cell
    """
    if cell.cell_type != "code":
        raise ValueError(
            f"Cell at index {index} is {cell.cell_type}, not a code cell"
        )


class _NotebookContext:
    """Context manager for atomic notebook operations.
    
    Handles read-modify-write atomically to reduce I/O operations.
    """
    
    def __init__(self, path: str):
        """Initialize notebook context.
        
        Args:
            path: Path to notebook file
        """
        self.path = _normalize_path(path)
        self.nb = None
    
    def __enter__(self):
        """Read notebook on enter."""
        self.nb = _read_notebook(self.path)
        return self.nb
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Write notebook on exit if no exception."""
        if exc_type is None:
            _write_notebook(self.path, self.nb)
        return False


def _read_notebook(notebook_path: str):
    """Read a notebook file and return the notebook object."""
    with open(notebook_path, "r", encoding="utf-8") as f:
        return nbformat.read(f, as_version=4)


def _write_notebook(notebook_path: str, nb) -> None:
    """Write a notebook object to a file."""
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)


# ============================================================================
# YDoc Collaborative Editing Support
# ============================================================================

async def _get_file_id(file_path: str) -> str | None:
    """Get the file ID for a document from the file ID manager.

    Args:
        file_path: Normalized absolute path to the document

    Returns:
        File ID string if available, None otherwise
    """
    if not YDOC_AVAILABLE:
        return None

    serverapp = get_server_context()
    if not serverapp:
        return None

    try:
        file_id_manager = serverapp.web_app.settings.get("file_id_manager")
        if not file_id_manager:
            return None

        file_id = file_id_manager.get_id(file_path)
        return file_id
    except Exception as e:
        logger.debug(f"Could not get file ID for {file_path}: {e}")
        return None


async def _get_ydoc_room(room_id: str):
    """Get a YDoc room by room ID.

    Args:
        room_id: The room identifier (format: "json:notebook:{file_id}")

    Returns:
        Room object if available, None otherwise
    """
    serverapp = get_server_context()
    if not serverapp:
        return None

    try:
        # Try jupyter-server-documents approach first (newer)
        yroom_manager = serverapp.web_app.settings.get("yroom_manager")
        if yroom_manager:
            if hasattr(yroom_manager, 'has_room') and yroom_manager.has_room(room_id):
                return yroom_manager.get_room(room_id)
            return None

        # Fallback to jupyter_server_ydoc approach (older)
        jupyter_server_ydoc = serverapp.web_app.settings.get("jupyter_server_ydoc")
        if jupyter_server_ydoc:
            ywebsocket_server = jupyter_server_ydoc.ywebsocket_server
            if hasattr(ywebsocket_server, 'room_exists') and ywebsocket_server.room_exists(room_id):
                return await ywebsocket_server.get_room(room_id)

        return None
    except Exception as e:
        logger.debug(f"Could not get room {room_id}: {e}")
        return None


async def _get_ynotebook(file_path: str):
    """Get YNotebook instance for a file if it's open in JupyterLab.

    Args:
        file_path: Normalized absolute path to notebook

    Returns:
        YNotebook instance if notebook is open, None otherwise
    """
    if not YDOC_AVAILABLE:
        return None

    try:
        # Get file ID
        file_id = await _get_file_id(file_path)
        if not file_id:
            return None

        # Get room
        room_id = f"json:notebook:{file_id}"
        yroom = await _get_ydoc_room(room_id)
        if not yroom:
            return None

        # Get YNotebook from room
        # Try new API first, fall back to old
        if hasattr(yroom, 'get_jupyter_ydoc'):
            return await yroom.get_jupyter_ydoc()
        elif hasattr(yroom, '_document'):
            return yroom._document
        else:
            return None

    except Exception as e:
        logger.debug(f"Could not get YNotebook for {file_path}: {e}")
        return None


def _get_cell_index_by_id_ydoc(ydoc, cell_id: str) -> int | None:
    """Get cell index from cell ID using YDoc.

    Args:
        ydoc: YNotebook instance
        cell_id: Cell UUID

    Returns:
        Cell index if found, None otherwise
    """
    try:
        cell_index, _ = ydoc.find_cell(cell_id)
        return cell_index
    except Exception:
        return None


def _get_cell_index_by_id_nbformat(nb, cell_id: str) -> int | None:
    """Get cell index from cell ID using nbformat notebook.

    Args:
        nb: nbformat notebook object
        cell_id: Cell UUID

    Returns:
        Cell index if found, None otherwise
    """
    for i, cell in enumerate(nb.cells):
        # Check both cell.id and cell.metadata.id
        if hasattr(cell, 'id') and cell.id == cell_id:
            return i
        if cell.get('id') == cell_id:
            return i
        cell_metadata_id = cell.get('metadata', {}).get('id')
        if cell_metadata_id == cell_id:
            return i
    return None


def _update_cell_outputs_ydoc(ydoc, cell_index: int, execution_count: int | None, outputs: list):
    """Update cell outputs in YDoc (for open notebooks).

    This updates the cell's execution count and outputs directly in YDoc memory,
    which makes changes immediately visible in JupyterLab without disk I/O.

    Args:
        ydoc: YNotebook instance
        cell_index: Index of cell to update
        execution_count: Execution counter from kernel
        outputs: List of output dicts from execution
    """
    if cell_index >= ydoc.cell_number:
        logger.warning(f"Cell index {cell_index} out of range in YDoc (has {ydoc.cell_number} cells)")
        return

    try:
        ycell = ydoc.ycells[cell_index]

        # Update execution count
        if execution_count is not None:
            ycell["execution_count"] = execution_count

        # Update outputs (YDoc expects list of dicts)
        if outputs is not None:
            ycell["outputs"] = outputs

        logger.debug(f"Updated cell {cell_index} outputs in YDoc (execution_count={execution_count})")

    except Exception as e:
        logger.warning(f"Failed to update cell outputs in YDoc: {e}")


async def _get_cell_by_id(notebook_path: str, cell_id: str) -> tuple[Any, int] | None:
    """Get cell and its index by cell ID, trying YDoc first.

    Args:
        notebook_path: Normalized path to notebook
        cell_id: Cell UUID

    Returns:
        Tuple of (cell, index) if found, None otherwise
    """
    # Try YDoc first if available
    ydoc = await _get_ynotebook(notebook_path)
    if ydoc:
        cell_index = _get_cell_index_by_id_ydoc(ydoc, cell_id)
        if cell_index is not None and 0 <= cell_index < ydoc.cell_number:
            return (ydoc.ycells[cell_index], cell_index)

    # Fallback to nbformat
    nb = _read_notebook(notebook_path)
    cell_index = _get_cell_index_by_id_nbformat(nb, cell_id)
    if cell_index is not None:
        return (nb.cells[cell_index], cell_index)

    return None


def set_server_context(serverapp):
    """Set the Jupyter ServerApp context for tools to use.

    Args:
        serverapp: The Jupyter ServerApp instance
    """
    _server_context["serverapp"] = serverapp
    logger.info("Server context set for notebook execution tools")


def get_server_context():
    """Get the Jupyter ServerApp context.

    Returns:
        The Jupyter ServerApp instance or None if not set
    """
    return _server_context.get("serverapp")


async def list_running_kernels() -> dict[str, Any]:
    """List all running Jupyter kernels.

    Returns a list of all currently running kernel sessions with their
    IDs, names, and current state.

    Returns:
        dict: A dictionary containing:
            - kernels: List of kernel information dicts with:
                - id: Kernel ID
                - name: Kernel name (e.g., 'python3')
                - last_activity: Last activity timestamp
                - execution_state: Current execution state
                - connections: Number of connections

    Example:
        >>> result = await list_running_kernels()
        >>> print(result)
        {
            "kernels": [
                {
                    "id": "abc123",
                    "name": "python3",
                    "last_activity": "2024-01-01T12:00:00",
                    "execution_state": "idle",
                    "connections": 1
                }
            ]
        }
    """
    serverapp = get_server_context()
    if serverapp is None:
        return {"error": "Server context not available", "kernels": []}

    try:
        kernel_manager = serverapp.kernel_manager

        # Get list of kernel IDs
        kernel_ids = list(kernel_manager.list_kernel_ids())

        kernels = []
        for kernel_id in kernel_ids:
            try:
                # Get kernel info
                kernel = kernel_manager.get_kernel(kernel_id)

                kernel_info = {
                    "id": kernel_id,
                    "name": kernel.kernel_name if hasattr(kernel, "kernel_name") else "unknown",
                    "last_activity": kernel.last_activity.isoformat() if hasattr(kernel, "last_activity") else None,
                    "execution_state": getattr(kernel, "execution_state", "unknown"),
                    "connections": kernel.connection_count if hasattr(kernel, "connection_count") else 0,
                }
                kernels.append(kernel_info)
            except Exception as e:
                logger.warning(f"Error getting info for kernel {kernel_id}: {e}")
                continue

        logger.info(f"Listed {len(kernels)} kernels")
        return {"kernels": kernels}

    except Exception as e:
        logger.error(f"Error listing kernels: {e}")
        return {"error": str(e), "kernels": []}


async def list_available_kernels() -> dict[str, Any]:
    """List all available (installed) kernel specifications.

    Returns information about all kernel specifications that are installed
    and available for use. This is useful for discovering what kernels
    can be used with setup_notebook or switch_notebook_kernel.

    This function can be used to check whether a specific kernel (e.g., 'python3',
    'ir', 'julia-1.9') is available before creating a notebook or starting a kernel.

    Returns:
        dict: A dictionary containing:
            - kernelspecs: Dict mapping kernel names to kernel info with:
                - name: Kernel name (e.g., 'python3', 'ir', 'julia-1.9')
                - resource_dir: Path to kernel resource directory
                - spec: Full kernel specification dict with:
                    - display_name: Human-readable name
                    - language: Programming language
                    - argv: Command line arguments to start kernel
                    - env: Environment variables (optional)
                    - metadata: Additional metadata (optional)

    Example:
        >>> result = await list_available_kernels()
        >>> print(result)
        {
            "kernelspecs": {
                "python3": {
                    "name": "python3",
                    "resource_dir": "/usr/local/share/jupyter/kernels/python3",
                    "spec": {
                        "display_name": "Python 3",
                        "language": "python",
                        "argv": ["/usr/bin/python3", "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                        "env": {},
                        "metadata": {}
                    }
                },
                "ir": {
                    "name": "ir",
                    "resource_dir": "/usr/local/share/jupyter/kernels/ir",
                    "spec": {
                        "display_name": "R",
                        "language": "R",
                        "argv": ["/usr/bin/R", "--slave", "-e", "IRkernel::main()", "--args", "{connection_file}"]
                    }
                }
            }
        }

        >>> # Check if a kernel is available
        >>> result = await list_available_kernels()
        >>> if "python3" in result["kernelspecs"]:
        ...     print("Python 3 kernel is available!")
    """
    try:
        # Try to use server context first for better integration
        serverapp = get_server_context()
        if serverapp is not None:
            ksm = serverapp.kernel_spec_manager
        else:
            # Fallback to standalone KernelSpecManager if server context not available
            from jupyter_client.kernelspec import KernelSpecManager
            ksm = KernelSpecManager()

        # Get all kernel specs
        all_specs = ksm.get_all_specs()
        
        # Format the specs for output
        kernelspecs = {}
        for kernel_name, spec_info in all_specs.items():
            kernelspecs[kernel_name] = {
                "name": kernel_name,
                "resource_dir": spec_info.get("resource_dir", ""),
                "spec": spec_info.get("spec", {})
            }
        
        logger.info(f"Listed {len(kernelspecs)} available kernel specifications")
        return {"kernelspecs": kernelspecs}

    except Exception as e:
        logger.error(f"Error listing kernel specifications: {e}")
        return {"error": str(e), "kernelspecs": {}}


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
    notebook_path = _normalize_path(notebook_path)

    serverapp = get_server_context()
    if serverapp is None:
        return {"message": "Server context not available"}

    # Get kernel ID if tracked
    kernel_id = _kernel_tracker.get(notebook_path)

    result = {"message": "Notebook session closed"}

    if kernel_id and shutdown_kernel:
        try:
            kernel_manager = serverapp.kernel_manager
            if kernel_id in kernel_manager.list_kernel_ids():
                await kernel_manager.shutdown_kernel(kernel_id)
                result["kernel_id"] = kernel_id
                logger.info(f"Shut down kernel {kernel_id} for notebook {notebook_path}")
        except Exception as e:
            logger.warning(f"Error shutting down kernel {kernel_id}: {e}")

    # Remove from tracking
    _kernel_tracker.untrack(notebook_path)

    return result


async def switch_notebook_kernel(
    notebook_path: str,
    kernel_name: str
) -> dict[str, Any]:
    """Switch a notebook to use a different kernel.

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
    notebook_path = _normalize_path(notebook_path)

    # First, shutdown existing kernel
    await shutdown_notebook(notebook_path, shutdown_kernel=True)

    # Determine language name based on kernel
    if kernel_name.lower() in ["ir", "r", "irkernel"]:
        language_name = "R"
        display_name = "R"
    elif kernel_name.startswith("python"):
        language_name = "python"
        display_name = f"Python 3" if kernel_name == "python3" else kernel_name
    elif kernel_name.startswith("julia"):
        language_name = "julia"
        display_name = kernel_name
    else:
        language_name = kernel_name
        display_name = kernel_name

    # Update notebook metadata atomically
    with _NotebookContext(notebook_path) as nb:
        nb.metadata["kernelspec"] = {
            "name": kernel_name,
            "display_name": display_name,
            "language": language_name
        }
        nb.metadata["language_info"] = {
            "name": language_name
        }

    # Start a new kernel with the specified kernel name
    serverapp = _require_server_context()
    kernel_manager = serverapp.kernel_manager
    kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)

    # Track the new kernel
    _kernel_tracker.track(notebook_path, kernel_id)

    logger.info(f"Switched notebook {notebook_path} to kernel {kernel_name} (ID: {kernel_id})")

    return {
        "message": f"Kernel switched to {kernel_name}",
        "kernel_name": kernel_name,
        "kernel_id": kernel_id
    }


async def _validate_execution_reply(
    shell_reply: dict,
    msg_id: str,
    timeout: int
) -> int | None:
    """Validate and extract execution_count from shell channel execute_reply.
    
    The execute_reply message ALWAYS contains execution_count, whereas
    execute_result on iopub only exists when code produces a result.
    
    Args:
        shell_reply: The shell reply message
        msg_id: Expected parent message ID
        timeout: Timeout value for logging
        
    Returns:
        execution_count if valid, None otherwise
    """
    # Validate this is the reply to our execution request
    if (shell_reply.get("parent_header", {}).get("msg_id") == msg_id and
        shell_reply.get("header", {}).get("msg_type") == "execute_reply"):
        reply_content = shell_reply.get("content", {})
        execution_count = reply_content.get("execution_count")
        logger.debug(f"Captured execution_count from shell reply: {execution_count}")

        # Check execution status
        if reply_content.get("status") == "error":
            logger.warning("Execute_reply indicates error status")
        
        return execution_count
    else:
        logger.warning(
            f"Shell reply validation failed - "
            f"msg_type={shell_reply.get('header', {}).get('msg_type')}, "
            f"expected parent_msg_id={msg_id}, "
            f"got={shell_reply.get('parent_header', {}).get('msg_id')}"
        )
        return None


async def _collect_outputs(
    client,
    msg_id: str,
    execution_count: int | None,
    timeout: int
) -> tuple[list[dict], str, str | None]:
    """Collect outputs from iopub channel.
    
    Args:
        client: Kernel client
        msg_id: Message ID to match
        execution_count: Execution count from shell reply (for validation)
        timeout: Maximum time to wait for outputs
        
    Returns:
        Tuple of (outputs, status, error_msg)
    """
    outputs = []
    status = "ok"
    error_msg = None

    # Wait for execution to complete
    start_time = asyncio.get_event_loop().time()
    while True:
        if asyncio.get_event_loop().time() - start_time > timeout:
            status = "error"
            error_msg = f"Execution timed out after {timeout} seconds"
            break

        try:
            # Directly await the async method - no executor needed for AsyncKernelClient
            msg = await asyncio.wait_for(
                client.get_iopub_msg(timeout=0.5),
                timeout=1
            )

            # Validate message structure
            if not isinstance(msg, dict):
                logger.error(f"Invalid message type from kernel: {type(msg)}")
                status = "error"
                error_msg = f"Invalid message type: {type(msg).__name__}"
                break

            try:
                msg_type = msg["header"]["msg_type"]
                content = msg["content"]
            except (KeyError, TypeError) as e:
                logger.error(f"Malformed kernel message: {e}")
                status = "error"
                error_msg = f"Malformed kernel message: {str(e)}"
                break

            # Check if this is a reply to our execute request
            if msg["parent_header"].get("msg_id") != msg_id:
                continue

            # Handle different message types
            if msg_type == "execute_result":
                # execution_count already captured from shell reply
                # execute_result's execution_count should match for validation
                result_exec_count = content.get("execution_count")
                if result_exec_count != execution_count:
                    logger.warning(
                        f"execution_count mismatch: shell={execution_count}, "
                        f"execute_result={result_exec_count}"
                    )

                outputs.append({
                    "output_type": "execute_result",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {}),
                    "execution_count": execution_count  # Use from shell reply
                })

            elif msg_type == "stream":
                outputs.append({
                    "output_type": "stream",
                    "name": content.get("name", "stdout"),
                    "text": content.get("text", "")
                })

            elif msg_type == "display_data":
                outputs.append({
                    "output_type": "display_data",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {})
                })

            elif msg_type == "error":
                status = "error"
                error_msg = "\n".join(content.get("traceback", []))
                outputs.append({
                    "output_type": "error",
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": content.get("traceback", [])
                })

            elif msg_type == "status":
                if content.get("execution_state") == "idle":
                    # Execution complete
                    break

        except asyncio.TimeoutError:
            # No message received in 1 second, check if we should continue
            continue

    return outputs, status, error_msg


async def _execute_code_on_kernel(
    client,
    code: str,
    kernel_id: str,
    timeout: int
) -> dict[str, Any]:
    """Execute code on kernel and collect results.
    
    Args:
        client: Kernel client with started channels
        code: Code to execute
        kernel_id: Kernel ID for logging
        timeout: Execution timeout
        
    Returns:
        dict with execution_count, outputs, status, and optional error
    """
    # Execute the code
    logger.info(f"Executing code in kernel {kernel_id}")
    msg_id = client.execute(code)

    # Get execution_count from execute_reply on shell channel
    execution_count = None
    try:
        shell_reply = await asyncio.wait_for(
            client.get_shell_msg(timeout=timeout),
            timeout=timeout + 1
        )
        execution_count = await _validate_execution_reply(shell_reply, msg_id, timeout)
    except asyncio.TimeoutError:
        logger.error(f"Timeout waiting for execute_reply on shell channel after {timeout}s")
    except Exception as e:
        logger.error(f"Error reading execute_reply from shell channel: {e}")

    # Collect outputs from iopub channel
    outputs, status, error_msg = await _collect_outputs(client, msg_id, execution_count, timeout)

    logger.info(f"Execution complete: status={status}, outputs={len(outputs)}")

    result = {
        "execution_count": execution_count,
        "outputs": outputs,
        "status": status
    }

    if error_msg:
        result["error"] = error_msg

    return result


async def execute_cell(
    code: str,
    kernel_id: str | None = None,
    timeout: int = 30,
    kernel_name: str = "python3"
) -> dict[str, Any]:
    """Execute code in a Jupyter kernel.

    Executes the provided code in a specified kernel or starts a new one.
    Returns the execution results including outputs, errors, and execution count.

    Args:
        code: The code to execute
        kernel_id: Optional kernel ID to use. If None, starts a new kernel.
        timeout: Maximum time in seconds to wait for execution (default: 30)
        kernel_name: Kernel name to use when starting new kernel (default: "python3")

    Returns:
        dict: A dictionary containing:
            - status: "ok" or "error"
            - kernel_id: ID of the kernel used
            - execution_count: Execution counter value
            - outputs: List of outputs (text, images, errors, etc.)
            - error: Error message if execution failed

    Example:
        >>> result = await execute_cell("print('Hello, World!')")
        >>> print(result)
        {
            "status": "ok",
            "kernel_id": "abc123",
            "execution_count": 1,
            "outputs": [{"output_type": "stream", "name": "stdout", "text": "Hello, World!\\n"}]
        }
    """
    serverapp = get_server_context()
    if serverapp is None:
        return {"error": "Server context not available", "status": "error"}

    kernel_manager = serverapp.kernel_manager
    started_kernel = False

    try:
        # Get or start kernel
        if kernel_id is None:
            # Start a new kernel
            logger.info(f"Starting new kernel: {kernel_name}")
            kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)
            started_kernel = True
            logger.info(f"Started kernel: {kernel_id}")

            # Wait a bit for kernel to be ready
            await asyncio.sleep(1)

        # Get the kernel
        kernel = kernel_manager.get_kernel(kernel_id)

        # Create a client to communicate with the kernel
        client = kernel.client()
        client.start_channels()

        try:
            # Wait for kernel to be ready
            await asyncio.wait_for(client.wait_for_ready(), timeout=10)

            # Execute the code and collect results
            exec_result = await _execute_code_on_kernel(client, code, kernel_id, timeout)

            # Add kernel info to result
            exec_result["kernel_id"] = kernel_id
            exec_result["started_new_kernel"] = started_kernel

            return exec_result

        finally:
            # Clean up client
            client.stop_channels()

    except Exception as e:
        logger.error(f"Error executing cell: {e}")
        return {
            "error": str(e),
            "status": "error",
            "kernel_id": kernel_id,
            "started_new_kernel": started_kernel
        }


async def _query_view_source(
    notebook_path: str,
    execution_count: int | None = None,
    position_index: int | None = None,
) -> dict | list[dict]:
    """View the source code of a Jupyter notebook (either single cell or all cells)."""
    if execution_count is not None and position_index is not None:
        raise ValueError("Cannot provide both execution_count and position_index.")

    # Normalize path
    notebook_path = _normalize_path(notebook_path)

    # Try YDoc first for active notebooks
    ydoc = await _get_ynotebook(notebook_path)
    if ydoc:
        logger.debug("Using YDoc for view_source")
        # Convert YDoc cells to standard format
        cells = []
        for i in range(ydoc.cell_number):
            ycell = ydoc.ycells[i]
            cell_dict = {
                "cell_type": ycell.get("cell_type", "code"),
                "source": str(ycell.get("source", "")),
                "metadata": dict(ycell.get("metadata", {})),
            }
            if cell_dict["cell_type"] == "code":
                cell_dict["execution_count"] = ycell.get("execution_count")
                cell_dict["outputs"] = list(ycell.get("outputs", []))
            cells.append(cell_dict)

        # View all cells if no specific cell requested
        if execution_count is None and position_index is None:
            logger.info("Viewing all cells from YDoc")
            return _filter_output_dicts(cells)

        # Find cell by execution_count
        if position_index is None and execution_count is not None:
            for i, cell in enumerate(cells):
                if cell.get("cell_type") == "code" and cell.get("execution_count") == execution_count:
                    position_index = i
                    break

            if position_index is None:
                raise ValueError(f"No cells found with execution count {execution_count}")

        # Validate and return cell
        if position_index >= len(cells):
            raise IndexError(f"Cell index {position_index} out of range (notebook has {len(cells)} cells)")

        return _filter_output_dicts([cells[position_index]])[0]

    # Fallback to nbformat
    try:
        nb = _read_notebook(notebook_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Notebook not found: {notebook_path}")

    # View all cells if no specific cell requested
    if execution_count is None and position_index is None:
        logger.info("Viewing all cells")
        return _filter_cell_outputs(nb.cells)

    # Find cell by execution_count if provided
    if position_index is None and execution_count is not None:
        position_indices = []
        for i, cell in enumerate(nb.cells):
            if cell.cell_type == "code" and cell.get("execution_count") == execution_count:
                position_indices.append(i)

        if len(position_indices) == 0:
            cells_info = _get_available_execution_counts(nb)
            error_msg = f"No cells found with execution count {execution_count}."
            if cells_info["execution_counts"]:
                error_msg += f" Available execution counts: {cells_info['execution_counts']}"
            else:
                error_msg += " No cells have been executed yet."
            raise ValueError(error_msg)
        elif len(position_indices) > 1:
            raise ValueError(f"Multiple cells found with execution count {execution_count}")

        position_index = position_indices[0]

    # Get cell by position index
    _validate_cell_index(nb, position_index, "view")

    return _filter_cell_outputs([nb.cells[position_index]])[0]


def _query_check_server() -> str:
    """Check if the Jupyter server is running and accessible."""
    serverapp = get_server_context()
    if serverapp is None:
        return "Jupyter server is not accessible"
    return "Jupyter server is running"


async def _query_list_sessions() -> list:
    """List all notebook sessions on the Jupyter server."""
    serverapp = _require_server_context()

    try:
        session_manager = serverapp.session_manager
        sessions = await session_manager.list_sessions()
        return list(sessions)
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return []


async def _query_get_position_index(
    notebook_path: str,
    execution_count: int | None = None,
    cell_id: str | None = None,
) -> int:
    """Get the index of a code cell in a Jupyter notebook."""
    if execution_count is None and cell_id is None:
        raise ValueError("Must provide either execution_count or cell_id (got neither).")
    if execution_count is not None and cell_id is not None:
        raise ValueError("Must provide either execution_count or cell_id (got both).")

    # Validate execution_count range
    if execution_count is not None:
        if execution_count < 1:
            raise ValueError(
                f"execution_count={execution_count} is invalid. Execution counts start from 1. "
                "If you meant to use position_index (which starts from 0), use position_index parameter instead."
            )
        elif execution_count > 10000:  # Reasonable upper bound
            raise ValueError(
                f"execution_count={execution_count} seems unreasonably high. "
                "Are you sure this is correct? Most notebooks have execution counts in the 1-100 range. "
                "If you meant to use position_index, use position_index parameter instead."
            )

    notebook_path = _normalize_path(notebook_path)

    # Read the notebook
    nb = _read_notebook(notebook_path)

    position_indices = []
    for i, cell in enumerate(nb.cells):
        if execution_count is not None and cell.cell_type == "code":
            if cell.get("execution_count") == execution_count:
                position_indices.append(i)
        elif cell_id is not None:
            if cell.get("id") == cell_id:
                position_indices.append(i)

    if len(position_indices) == 0:
        cells_info = _get_available_execution_counts(nb)
        error_parts = []

        if execution_count is not None:
            error_parts.append(f"No cell found with execution_count={execution_count}")
            if cells_info["execution_counts"]:
                available_counts = ", ".join(map(str, cells_info["execution_counts"]))
                error_parts.append(f"Available execution_counts: [{available_counts}]")
            else:
                error_parts.append("No cells have been executed yet (all execution_counts are None)")

            error_parts.append(f"Notebook has {cells_info['total_cells']} total cells (positions 0-{cells_info['total_cells'] - 1})")
            if cells_info["code_cells"] > 0:
                error_parts.append(f"Including {cells_info['code_cells']} code cells")
            if cells_info["unexecuted_cells"] > 0:
                error_parts.append(f"with {cells_info['unexecuted_cells']} unexecuted")

            error_parts.append("Try using position_index instead, or execute the cell first to get an execution_count")

        if cell_id is not None:
            error_parts.append(f"No cell found with cell_id={cell_id}")

        raise ValueError(". ".join(error_parts))

    elif len(position_indices) > 1:
        raise ValueError(f"Found {len(position_indices)} cells matching the criteria at positions {sorted(position_indices)}.")

    return position_indices[0]


def _convert_outputs_to_notebook_nodes(outputs: list[dict]) -> list:
    """Convert plain dict outputs to nbformat NotebookNode objects.

    Args:
        outputs: List of output dicts from execute_cell

    Returns:
        List of nbformat NotebookNode objects
    """
    return [from_dict(output) for output in outputs]

def _get_available_execution_counts(nb) -> dict:
    """Get comprehensive cell information for better error messages.

    Args:
        nb: Notebook object

    Returns:
        dict: Contains:
            - execution_counts: list of actual execution counts (excluding None)
            - position_indices: list of all position indices
            - cell_types: list of cell types for each position
            - total_cells: total number of cells
            - code_cells: number of code cells
            - unexecuted_cells: number of code cells without execution count
    """
    cells_info = {
        "execution_counts": [],
        "position_indices": [],
        "cell_types": [],
        "total_cells": 0,
        "code_cells": 0,
        "unexecuted_cells": 0,
    }

    for i, cell in enumerate(nb.cells):
        cells_info["position_indices"].append(i)
        cell_type = cell.cell_type
        cells_info["cell_types"].append(cell_type)
        cells_info["total_cells"] += 1

        if cell_type == "code":
            cells_info["code_cells"] += 1
            execution_count = cell.get("execution_count")
            if execution_count is not None:
                cells_info["execution_counts"].append(execution_count)
            else:
                cells_info["unexecuted_cells"] += 1

    # Sort execution counts for better presentation
    cells_info["execution_counts"].sort()
    return cells_info


def _filter_output_dicts(outputs: list[dict]) -> list[dict]:
    """Filter large binary data from output dicts to reduce MCP response size.
    
    This is used when we already have plain dict outputs (e.g., from execute_cell)
    and need to filter them before returning in MCP response. This prevents huge
    token counts when outputs contain base64-encoded images or large HTML. The actual
    data is still saved to the notebook file, just not transmitted in the MCP response.
    
    Args:
        outputs: List of output dicts
        
    Returns:
        List of filtered output dicts with large binary data replaced by placeholders
    """
    filtered_outputs = []
    
    for output in outputs:
        # Create a copy to avoid modifying the original
        filtered_output = dict(output)
        
        # Check if this output has data that might be large
        if output.get("output_type") in ("display_data", "execute_result"):
            data = output.get("data", {})
            if data:
                data_types = list(data.keys())
                
                # Filter out large binary image data
                if any("image" in dt or "png" in dt or "jpeg" in dt or "svg" in dt for dt in data_types):
                    filtered_output["data"] = {
                        "[filtered]": f"Image data present ({', '.join(data_types)}). Data saved to notebook but not transmitted to reduce token count."
                    }
                # Filter out large HTML
                elif any("html" in dt for dt in data_types):
                    # Keep text/plain if present, filter HTML
                    filtered_data = {}
                    for key, value in data.items():
                        if "html" in key:
                            filtered_data["[filtered]"] = f"HTML data present. Data saved to notebook but not transmitted to reduce token count."
                        else:
                            filtered_data[key] = value
                    filtered_output["data"] = filtered_data if filtered_data else {
                        "[filtered]": f"HTML data present ({', '.join(data_types)})"
                    }
        
        filtered_outputs.append(filtered_output)
    
    return filtered_outputs


def _filter_cell_outputs(cells: list) -> list[dict]:
    """Filter out verbose output data from cells, keeping only source and basic metadata.

    This prevents huge token counts when outputs contain base64-encoded images or
    large HTML. The actual data is still saved to the notebook file, just not
    transmitted in the MCP response.
    
    Args:
        cells: List of notebook cells (can be NotebookNode objects or plain dicts)

    Returns:
        List of filtered cell dicts with large binary data replaced by placeholders
    """
    filtered_cells = []

    for cell in cells:
        filtered_cell = {
            "cell_type": cell.cell_type,
            "source": cell.source,
            "metadata": cell.get("metadata", {}),
        }

        # For code cells, keep execution_count but filter outputs
        if cell.cell_type == "code":
            filtered_cell["execution_count"] = cell.get("execution_count")

            # Convert NotebookNode outputs to dicts and filter large data
            if hasattr(cell, "outputs") and cell.outputs:
                filtered_outputs = []
                
                for output in cell.outputs:
                    # Convert NotebookNode to dict
                    output_dict = {"output_type": output.output_type}

                    # Copy relevant attributes to dict
                    if hasattr(output, "text"):
                        output_dict["text"] = output.text
                    if hasattr(output, "name"):
                        output_dict["name"] = output.name
                    if hasattr(output, "execution_count"):
                        output_dict["execution_count"] = output.execution_count
                    if hasattr(output, "ename"):
                        output_dict["ename"] = output.ename
                    if hasattr(output, "evalue"):
                        output_dict["evalue"] = output.evalue
                    if hasattr(output, "traceback"):
                        output_dict["traceback"] = output.traceback
                    
                    # Handle data field with filtering for large content
                    if hasattr(output, "data"):
                        data = dict(output.data)
                        data_types = list(data.keys())
                        
                        # Filter out large binary image data
                        if any("image" in dt or "png" in dt or "jpeg" in dt or "svg" in dt for dt in data_types):
                            output_dict["data"] = {
                                "[filtered]": f"Image data present ({', '.join(data_types)}). Data saved to notebook but not transmitted to reduce token count."
                            }
                        # Filter out large HTML
                        elif any("html" in dt for dt in data_types):
                            # Keep text/plain if present, filter HTML
                            filtered_data = {}
                            for key, value in data.items():
                                if "html" in key:
                                    filtered_data["[filtered]"] = f"HTML data present. Data saved to notebook but not transmitted to reduce token count."
                                else:
                                    filtered_data[key] = value
                            output_dict["data"] = filtered_data if filtered_data else {
                                "[filtered]": f"HTML data present ({', '.join(data_types)})"
                            }
                        else:
                            output_dict["data"] = data
                    
                    # Copy metadata if present
                    if hasattr(output, "metadata"):
                        output_dict["metadata"] = dict(output.metadata)

                    filtered_outputs.append(output_dict)

                filtered_cell["outputs"] = filtered_outputs

        filtered_cells.append(filtered_cell)

    return filtered_cells


async def _execute_and_update_cell(
    notebook_path: str,
    cell_index: int,
    code: str,
    kernel_id: str | None = None,
    timeout: int = 30
) -> dict[str, Any]:
    """Execute code and update cell with results in one atomic operation.

    Now YDoc-aware: updates outputs in YDoc for open notebooks,
    or in disk file for closed notebooks. This prevents the bug where
    disk writes would overwrite YDoc changes and delete cells.

    This consolidates the common pattern of:
    1. Execute code in kernel
    2. Track new kernel if started
    3. Update notebook cell with results (via YDoc or nbformat)
    4. Filter outputs for response

    Args:
        notebook_path: Normalized path to notebook
        cell_index: Index of cell to update
        code: Code to execute
        kernel_id: Optional kernel ID to use
        timeout: Execution timeout

    Returns:
        dict: Execution results with filtered outputs
    """
    # Execute code
    exec_results = await execute_cell(code, kernel_id=kernel_id, timeout=timeout)
    
    # Ensure exec_results is a dict
    if not isinstance(exec_results, dict):
        logger.error(f"exec_results is not a dict: {type(exec_results)}")
        if inspect.iscoroutine(exec_results):
            logger.error("exec_results is a coroutine - execute_cell was not properly awaited")
        raise TypeError(f"execute_cell returned {type(exec_results)} instead of dict")
    
    # Track kernel if new
    if exec_results.get("started_new_kernel"):
        _kernel_tracker.track(notebook_path, exec_results["kernel_id"])

    # Try YDoc first for collaborative editing
    ydoc = await _get_ynotebook(notebook_path)

    if ydoc:
        # Update outputs in YDoc (memory-based, collaborative)
        logger.info(f"Updating cell {cell_index} outputs via YDoc")
        _update_cell_outputs_ydoc(
            ydoc,
            cell_index,
            exec_results.get("execution_count"),
            exec_results.get("outputs", [])
        )

        # IMPORTANT: Also persist to disk so outputs are saved in the notebook file
        # YDoc updates are in-memory only and won't survive if JupyterLab closes
        # We do this AFTER YDoc update to ensure user sees outputs immediately in UI
        logger.debug(f"Persisting cell {cell_index} outputs to disk")
        with _NotebookContext(notebook_path) as nb:
            if cell_index < len(nb.cells):
                cell = nb.cells[cell_index]
                if exec_results.get("execution_count") is not None:
                    cell.execution_count = exec_results["execution_count"]
                if exec_results.get("outputs"):
                    cell.outputs = _convert_outputs_to_notebook_nodes(exec_results["outputs"])
            else:
                logger.warning(f"Cell index {cell_index} out of range in disk notebook")
    else:
        # Fallback: Update notebook cell atomically on disk
        logger.debug(f"Updating cell {cell_index} outputs via nbformat")
        with _NotebookContext(notebook_path) as nb:
            if cell_index < len(nb.cells):
                cell = nb.cells[cell_index]
                if exec_results.get("execution_count") is not None:
                    cell.execution_count = exec_results["execution_count"]
                # Save UNFILTERED outputs to notebook file
                if exec_results.get("outputs"):
                    cell.outputs = _convert_outputs_to_notebook_nodes(exec_results["outputs"])
            else:
                logger.warning(f"Cell index {cell_index} out of range after reading notebook")
    
    # Return results with filtered outputs for MCP response
    filtered_results = {
        "status": exec_results.get("status"),
        "kernel_id": exec_results.get("kernel_id"),
        "execution_count": exec_results.get("execution_count"),
        "outputs": _filter_output_dicts(exec_results.get("outputs", [])),
        "started_new_kernel": exec_results.get("started_new_kernel", False)
    }
    
    if "error" in exec_results:
        filtered_results["error"] = exec_results["error"]
    
    return filtered_results


async def _modify_add_code_cell(
    notebook_path: str,
    cell_content: str,
    execute: bool = True,
    position_index: int | None = None,
) -> dict:
    """Add (and optionally execute) a code cell in a Jupyter notebook.

    If you are trying to fix a cell that previously threw an error,
    you should default to editing the cell vs adding a new one.

    Note that adding a cell without executing it leaves it with no execution_count which can make
    it slightly trickier to execute in a subsequent request, but goose can now find cells by
    cell_id and content as well, now that it can view the full notebook source.

    A motivating example for why this is state-dependent: user asks goose to write a function,
    user then manually modifies that function signature, then user asks goose to call that function
    in a new cell. If goose's knowledge is outdated, it will likely use the old signature.
    """
    if not cell_content:
        raise ValueError("cell_content is required for add_code operation")

    logger.info("Adding code cell")
    notebook_path = _normalize_path(notebook_path)

    # Try YDoc first for collaborative editing
    ydoc = await _get_ynotebook(notebook_path)
    inserted_index = None

    if ydoc:
        logger.info("Using YDoc for collaborative add_code_cell")
        try:
            # Create new cell dict
            cell_dict = {
                "cell_type": "code",
                "source": cell_content,
                "metadata": {},
                "outputs": [],
                "execution_count": None
            }

            # Create YCell
            ycell = ydoc.create_ycell(cell_dict)

            # Insert at position or append
            if position_index is not None:
                if position_index > ydoc.cell_number:
                    position_index = ydoc.cell_number
                ydoc.ycells.insert(position_index, ycell)
                inserted_index = position_index
            else:
                ydoc.ycells.append(ycell)
                inserted_index = ydoc.cell_number - 1

            logger.info(f"Added cell via YDoc at position {inserted_index}")

        except Exception as e:
            logger.warning(f"YDoc add failed, falling back to nbformat: {e}")
            ydoc = None  # Force fallback

    # Fallback to nbformat
    if not ydoc:
        with _NotebookContext(notebook_path) as nb:
            new_cell = new_code_cell(source=cell_content)

            # Insert at position or append
            if position_index is not None:
                if position_index > len(nb.cells):
                    position_index = len(nb.cells)
                nb.cells.insert(position_index, new_cell)
                inserted_index = position_index
            else:
                nb.cells.append(new_cell)
                inserted_index = len(nb.cells) - 1

    results = {"message": f"Code cell added at position {inserted_index}"}

    # Execute if requested
    if execute:
        try:
            logger.info("Cell added successfully, now executing")
            kernel_id = _kernel_tracker.get(notebook_path)

            # Use the new consolidated execution helper
            exec_results = await _execute_and_update_cell(
                notebook_path,
                inserted_index,
                cell_content,
                kernel_id
            )

            # Merge execution results into response
            results.update(exec_results)

        except Exception as e:
            import traceback
            logger.error(f"Error during execution: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            results["error"] = str(e)
            results["status"] = "error"

    return results


async def _modify_edit_code_cell(
    notebook_path: str,
    position_index: int,
    cell_content: str,
    execute: bool = True
) -> dict:
    """Edit a code cell in a Jupyter notebook.

    Note that users can edit cell contents too, so if you are making assumptions about the
    position_index of the cell to edit based on chat history with the user, you should first
    make sure the notebook state matches your expected state using your query_notebook tool.
    If it does not match the expected state, you should then use your query_notebook tool to update
    your knowledge of the current cell contents.

    If you execute a cell and it fails and you want to debug it, you should default to editing
    the existing cell vs adding a new cell each time you want to execute code.

    A motivating example for why this is state-dependent: user asks goose to write a function,
    user then manually modifies the function, then asks goose to make additional changes to the
    function. If goose's knowledge is outdated, it will likely ignore the user's recent changes
    and modify the old version of the function, losing user work.

    """
    if not cell_content:
        raise ValueError("cell_content is required for edit_code operation")
    if position_index is None:
        raise ValueError("position_index is required for edit_code operation")

    logger.info("Editing code cell")
    notebook_path = _normalize_path(notebook_path)

    # Try YDoc first for collaborative editing
    ydoc = await _get_ynotebook(notebook_path)

    if ydoc:
        logger.info("Using YDoc for collaborative edit_code_cell")
        try:
            # Validate index
            if position_index >= ydoc.cell_number:
                raise IndexError(f"Cell index {position_index} out of range (notebook has {ydoc.cell_number} cells)")

            # Get the cell
            ycell = ydoc.ycells[position_index]

            # Update cell source (simply replace the entire content)
            ysource = ycell.get("source")
            if isinstance(ysource, YText):
                # Clear existing content and set new content
                ysource.clear()
                ysource.extend(cell_content)
            else:
                ycell["source"] = cell_content

            # Clear execution data
            ycell["execution_count"] = None
            ycell["outputs"] = []

            logger.info(f"Edited cell via YDoc at position {position_index}")

        except Exception as e:
            logger.warning(f"YDoc edit failed, falling back to nbformat: {e}")
            ydoc = None  # Force fallback

    # Fallback to nbformat
    if not ydoc:
        with _NotebookContext(notebook_path) as nb:
            _validate_cell_index(nb, position_index, "edit")

            # Update cell source and clear previous execution
            nb.cells[position_index].source = cell_content
            nb.cells[position_index].execution_count = None
            nb.cells[position_index].outputs = []

    results = {"message": f"Code cell at position {position_index} edited"}

    # Execute if requested
    if execute:
        try:
            kernel_id = _kernel_tracker.get(notebook_path)

            # Use the new consolidated execution helper
            exec_results = await _execute_and_update_cell(
                notebook_path,
                position_index,
                cell_content,
                kernel_id
            )

            # Merge execution results into response
            results.update(exec_results)

        except Exception as e:
            logger.error(f"Error during code cell edit execution: {e}")
            results["error"] = str(e)
            results["status"] = "error"

    return results


async def _modify_add_markdown_cell(
    notebook_path: str,
    cell_content: str,
    position_index: int | None = None
) -> dict:
    """Add a markdown cell in a Jupyter notebook.

    Technically might be a little risky to mark this as refreshes_state because the user could make
    other changes that are invisible to goose. But trying it out this way because I don't think
    goose adding a markdown cell should necessarily force it to view the full notebook source on
    subsequent tool calls.
    """
    if not cell_content:
        raise ValueError("cell_content is required for add_markdown operation")

    logger.info("Adding markdown cell")
    notebook_path = _normalize_path(notebook_path)

    try:
        # Try YDoc first for collaborative editing
        ydoc = await _get_ynotebook(notebook_path)

        if ydoc:
            logger.info("Using YDoc for collaborative add_markdown_cell")
            # Create new markdown cell dict
            cell_dict = {
                "cell_type": "markdown",
                "source": cell_content,
                "metadata": {}
            }

            # Create YCell
            ycell = ydoc.create_ycell(cell_dict)

            # Insert at position or append
            if position_index is not None:
                if position_index > ydoc.cell_number:
                    position_index = ydoc.cell_number
                ydoc.ycells.insert(position_index, ycell)
                message = f"Markdown cell inserted at position {position_index}"
            else:
                ydoc.ycells.append(ycell)
                message = "Markdown cell added"

        else:
            # Fallback to nbformat
            with _NotebookContext(notebook_path) as nb:
                new_cell = new_markdown_cell(source=cell_content)

                # Insert at position or append
                if position_index is not None:
                    if position_index > len(nb.cells):
                        position_index = len(nb.cells)
                    nb.cells.insert(position_index, new_cell)
                    message = f"Markdown cell inserted at position {position_index}"
                else:
                    nb.cells.append(new_cell)
                    message = "Markdown cell added"

        return {"message": message, "error": ""}

    except Exception as e:
        logger.error(f"Error adding markdown cell: {e}")
        return {"message": "", "error": str(e)}


async def _modify_edit_markdown_cell(
    notebook_path: str,
    position_index: int,
    cell_content: str
) -> dict:
    """Edit an existing markdown cell in a Jupyter notebook.

     Note that users can edit cell contents too, so if you are making assumptions about the
    position_index of the cell to edit based on chat history with the user, you should first
    make sure the notebook state matches your expected state using your query_notebook tool.
    If it does not match the expected state, you should then use your query_notebook tool to update
    your knowledge of the current cell contents.

    Args:
        notebook_path: Path to the notebook file (.ipynb extension will be added if missing),
                       relative to the Jupyter server root.
        position_index: positional index that NBModelClient uses under the hood.
        cell_content: New markdown content to write to the cell.

    Returns
    -------
        dict: Contains two keys:
            - "message": "Markdown cell edited" if successful, empty string if failed
            - "error": Error message if failed, empty string if successful

    Raises
    ------
        McpError: If notebook state has changed since last viewed
        McpError: If there's an error connecting to the Jupyter server
        IndexError: If position_index is out of range


    """
    if not cell_content:
        raise ValueError("cell_content is required for edit_markdown operation")
    if position_index is None:
        raise ValueError("position_index is required for edit_markdown operation")

    logger.info("Editing markdown cell")
    notebook_path = _normalize_path(notebook_path)

    try:
        # Try YDoc first for collaborative editing
        ydoc = await _get_ynotebook(notebook_path)

        if ydoc:
            logger.info("Using YDoc for collaborative edit_markdown_cell")
            # Validate index
            if position_index >= ydoc.cell_number:
                raise IndexError(f"Cell index {position_index} out of range (notebook has {ydoc.cell_number} cells)")

            # Get the cell
            ycell = ydoc.ycells[position_index]

            # Update cell source
            ysource = ycell.get("source")
            if isinstance(ysource, YText):
                ysource.clear()
                ysource.extend(cell_content)
            else:
                ycell["source"] = cell_content

        else:
            # Fallback to nbformat
            with _NotebookContext(notebook_path) as nb:
                _validate_cell_index(nb, position_index, "edit")
                nb.cells[position_index].source = cell_content

        return {"message": "Markdown cell edited", "error": ""}

    except Exception as e:
        logger.error(f"Error editing markdown cell: {e}")
        return {"message": "", "error": str(e)}


async def _modify_delete_cell(notebook_path: str, position_index: int) -> dict:
    """Delete a cell in a Jupyter notebook.

    Note that users can edit cell contents too, so if you assume you know the position_index
    of the cell to delete based on past chat history, you should first make sure the notebook state
    matches your expected state using your query_notebook tool. If it does not match the
    expected state, you should then use your query_notebook tool to update your knowledge of the
    current cell contents.

    A motivating example for why this is state-dependent: user asks goose to add a new cell,
    then user runs a few cells manually (changing execution_counts), then tells goose
    "now delete it". In the context of the conversation, this looks fine and Goose may assume it
    knows the correct position_index already, but its knowledge is outdated.
    """
    if position_index is None:
        raise ValueError("position_index is required for delete operation")

    notebook_path = _normalize_path(notebook_path)

    try:
        # Try YDoc first for collaborative editing
        ydoc = await _get_ynotebook(notebook_path)

        if ydoc:
            logger.info("Using YDoc for collaborative delete_cell")
            # Validate index
            if position_index >= ydoc.cell_number:
                raise IndexError(f"Cell index {position_index} out of range (notebook has {ydoc.cell_number} cells)")

            # Delete the cell
            del ydoc.ycells[position_index]

        else:
            # Fallback to nbformat
            with _NotebookContext(notebook_path) as nb:
                _validate_cell_index(nb, position_index, "delete")
                del nb.cells[position_index]

        return {"message": "Cell deleted", "error": ""}

    except Exception as e:
        return {"message": "", "error": str(e)}

async def _execute_cell_by_position(notebook_path: str, position_index: int, timeout: int = 30) -> dict:
    """Execute an existing code cell in a Jupyter notebook by position.

    In most cases you should call modify_notebook_cells instead, but occasionally
    you might want to re-execute a cell after changing a *different* cell.

    Note that users can edit cell contents too, so if you assume you know the position_index
    of the cell to execute based on past chat history, you should first make sure the notebook state
    matches your expected state using your query_notebook tool. If it does not match the
    expected state, you should then use your query_notebook tool to update your knowledge of the
    current cell contents.

    Technically could be considered state_dependent, but it is usually called inside edit_code_cell
    or add_code_cell which area already state_dependent. Every hash update is slow because we have
    to wait for the notebook to save first so using refreshes_state instead saves 1.5s per call.
    Only risk is if user asks goose to execute a single cell and goose assumes it knows the
    position_index already, but usually it would be faster for the user to just execute the cell
    directly - this tool is mostly useful to allow goose to debug independently.

    """
    if position_index is None:
        raise ValueError("position_index is required for execute_cell operation")

    notebook_path = _normalize_path(notebook_path)

    # Read the cell to execute
    nb = _read_notebook(notebook_path)
    _validate_cell_index(nb, position_index, "execute")
    
    cell = nb.cells[position_index]
    _validate_code_cell(cell, position_index)

    # Use tracked kernel if available
    kernel_id = _kernel_tracker.get(notebook_path)

    try:
        # Use the new consolidated execution helper
        result = await _execute_and_update_cell(
            notebook_path,
            position_index,
            cell.source,
            kernel_id,
            timeout
        )
        
        return result

    except Exception as e:
        import traceback
        logger.error(f"Error executing cell by position: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "error": str(e),
            "status": "error",
            "kernel_id": kernel_id,
            "started_new_kernel": False
        }

async def _execute_install_packages(notebook_path: str, package_names: str) -> str:
    """Install one or more packages using pip in the notebook environment.

    Unlike add_code_cell, this shouldn't rely on other code written in the notebook, so we mark
    it as refreshes_state rather than state_dependent. Assumes 'uv' is available in the
    environment where the Jupyter kernel is running.
    """
    if not package_names:
        raise ValueError("package_names is required for install_packages operation")

    logger.info(f"Installing packages: {package_names}")
    notebook_path = _normalize_path(notebook_path)

    try:
        # Create installation command
        cell_content = f"!pip install {package_names}"

        # Use tracked kernel if available
        kernel_id = _kernel_tracker.get(notebook_path)

        # Execute the cell and await the result
        result = await execute_cell(cell_content, kernel_id=kernel_id)

        # Ensure result is a dict
        if not isinstance(result, dict):
            logger.error(f"result is not a dict: {type(result)}, value: {result}")
            return f"Error: execute_cell returned {type(result).__name__} instead of dict"

        # Track the kernel if it was newly created
        if result.get("started_new_kernel"):
            _kernel_tracker.track(notebook_path, result.get("kernel_id"))

        # Extract output to see if installation was successful
        outputs = result.get("outputs", [])
        if len(outputs) == 0:
            installation_result = "No output from installation command"
        else:
            # Combine text from all outputs
            installation_result = []
            for output in outputs:
                if output.get("output_type") == "stream":
                    installation_result.append(output.get("text", ""))
                elif output.get("output_type") == "error":
                    installation_result.append("\n".join(output.get("traceback", [])))

        return f"Installation of packages [{package_names}]: {installation_result}"

    except Exception as e:
        import traceback
        logger.error(f"Error installing packages: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f"Error: {str(e)}"

async def query_notebook(
    notebook_path: str,
    query_type: str,
    execution_count: int | None = None,
    position_index: int | None = None,
    cell_id: str | None = None,
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
    if query_type == "view_source":
        return await _query_view_source(notebook_path, execution_count, position_index)
    elif query_type == "check_server":
        return _query_check_server()
    elif query_type == "list_sessions":
        return await _query_list_sessions()
    elif query_type == "list_kernels":
        return await list_available_kernels()
    elif query_type == "get_position_index":
        return await _query_get_position_index(notebook_path, execution_count, cell_id)
    else:
        raise ValueError(
            f"Invalid query_type: {query_type}. Must be one of: view_source, check_server, list_sessions, list_kernels, get_position_index"
        )


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
        position_index: Cell position (0-indexed). Examples: 0 (first cell), 1 (second cell)
        cell_id: Cell UUID (e.g., "205658d6-093c-4722-854c-90b149f254ad")
        execution_count: Execution count shown in Jupyter UI (e.g., [3])

    Returns:
        dict: Cell information containing:
            - cell_type: "code", "markdown", or "raw"
            - source: Cell source code/content
            - metadata: Cell metadata dict
            - execution_count: (code cells only) Execution counter
            - outputs: (code cells only) Filtered cell outputs

    Examples:
        # Read first cell
        cell = await read_cell("analysis.ipynb", position_index=0)

        # Read cell by UUID
        cell = await read_cell("analysis.ipynb", cell_id="abc-123-def")

        # Read cell by execution count
        cell = await read_cell("analysis.ipynb", execution_count=3)

    Raises:
        ValueError: If no identifier provided or multiple identifiers provided
        IndexError: If position_index is out of range
        FileNotFoundError: If notebook doesn't exist
    """
    # Validate exactly one identifier is provided
    identifiers = [position_index is not None, cell_id is not None, execution_count is not None]
    if sum(identifiers) == 0:
        raise ValueError("Must provide one of: position_index, cell_id, or execution_count")
    if sum(identifiers) > 1:
        raise ValueError("Provide only ONE of: position_index, cell_id, or execution_count")

    # Normalize path
    notebook_path = _normalize_path(notebook_path)

    # If cell_id provided, resolve to position_index first
    if cell_id is not None:
        result = await _get_cell_by_id(notebook_path, cell_id)
        if result is None:
            raise ValueError(f"Cell with ID {cell_id} not found in notebook")
        _, position_index = result

    # Use query_notebook for the actual read
    return await _query_view_source(notebook_path, execution_count, position_index)


async def execute_notebook_code(
    notebook_path: str,
    execution_type: str,
    position_index: int | None = None,
    package_names: str | None = None,
) -> dict[str, Any] | str:
    """Execute code in a Jupyter notebook.

    This consolidates all code execution operations into a single tool following MCP best practices.

    IMPORTANT: 
    ----------------------------------
    This tool requires that you first call setup_notebook with the correct server URL:

    Required setup:
        setup_notebook(\"my_notebook\", server_url=\"http://localhost:9999\", kernel_name=\"python3\")

    Then you can use this tool:
        execute_notebook_code(\"my_notebook\", \"execute_cell\", position_index=0)

    Without setup_notebook, this will try to connect to http://localhost:8888 by default.


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
    if execution_type == "execute_cell":
        return await _execute_cell_by_position(notebook_path, position_index)
    elif execution_type == "install_packages":
        return await _execute_install_packages(notebook_path, package_names)
    else:
        raise ValueError(
            f"Invalid execution_type: {execution_type}. Must be one of: execute_cell, install_packages"
        )


async def modify_notebook_cells(
    notebook_path: str,
    operation: str,
    cell_content: str | None = None,
    position_index: int | None = None,
    execute: bool = True,
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
    if operation == "add_code":
        return await _modify_add_code_cell(notebook_path, cell_content, execute, position_index)
    elif operation == "edit_code":
        return await _modify_edit_code_cell(notebook_path, position_index, cell_content, execute)
    elif operation == "add_markdown":
        return await _modify_add_markdown_cell(notebook_path, cell_content, position_index)
    elif operation == "edit_markdown":
        return await _modify_edit_markdown_cell(notebook_path, position_index, cell_content)
    elif operation == "delete":
        return await _modify_delete_cell(notebook_path, position_index)
    else:
        raise ValueError(
            f"Invalid operation: {operation}. Must be one of: add_code, edit_code, add_markdown, edit_markdown, delete"
        )


async def setup_notebook(
    notebook_path: str,
    kernel_name: str = "python3"
) -> dict[str, Any]:
    """Prepare notebook for use and connect to the kernel.

    Will create a new empty Jupyter notebook if it doesn't exist.

    **CALL THIS FIRST** - This tool must be called before using other notebook tools
    to establish the server URL connection. All subsequent notebook operations will
    use the server URL stored by this tool.

    This tool creates an empty notebook. To add content, use the modify_notebook_cells
    tool after creation:

    Example usage:
        # Step 1: Setup notebook with Python kernel
        await setup_notebook("demo.ipynb")

        # Step 2: Add cells
        await modify_notebook_cells("demo.ipynb", "add_markdown", "# Title\n\nDescription")
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
    notebook_path = _normalize_path(notebook_path)

    # Check if notebook exists
    notebook_exists = os.path.exists(notebook_path)

    # Get available kernels and validate/normalize kernel_name
    available_kernels = await list_available_kernels()
    kernelspecs = available_kernels.get("kernelspecs", {})

    # Normalize R kernel names
    if kernel_name.lower() in ["r", "irkernel"]:
        # Find the actual R kernel name
        for kname in kernelspecs:
            spec = kernelspecs[kname].get("spec", {})
            if spec.get("language", "").lower() == "r":
                kernel_name = kname
                break
    
    # Validate kernel exists
    if kernel_name not in kernelspecs:
        available_names = list(kernelspecs.keys())
        logger.warning(f"Kernel '{kernel_name}' not found. Available: {available_names}. Falling back to python3")
        kernel_name = "python3"

    if not notebook_exists:
        # Create new empty notebook
        logger.info(f"Creating new notebook: {notebook_path}")

        # Ensure directory exists
        dir_path = os.path.dirname(notebook_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # Determine language name based on kernel
        if kernel_name.lower() in ["ir", "r", "irkernel"]:
            language_name = "R"
            display_name = "R"
        elif kernel_name.startswith("python"):
            language_name = "python"
            display_name = f"Python 3" if kernel_name == "python3" else kernel_name
        elif kernel_name.startswith("julia"):
            language_name = "julia"
            display_name = kernel_name
        else:
            language_name = kernel_name
            display_name = kernel_name

        # Create empty notebook
        nb = new_notebook(
            cells=[],
            metadata={
                "kernelspec": {
                    "name": kernel_name,
                    "display_name": display_name,
                    "language": language_name
                },
                "language_info": {
                    "name": language_name
                }
            }
        )

        _write_notebook(notebook_path, nb)

        message = f"Notebook {notebook_path} created with kernel {kernel_name}"
    else:
        logger.info(f"Notebook {notebook_path} already exists")
        message = f"Notebook {notebook_path} already exists"

    # Start a kernel for this notebook
    serverapp = get_server_context()
    if serverapp:
        try:
            kernel_manager = serverapp.kernel_manager
            kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)
            _kernel_tracker.track(notebook_path, kernel_id)
            logger.info(f"Started kernel {kernel_name} (ID: {kernel_id}) for notebook {notebook_path}")
        except Exception as e:
            logger.warning(f"Could not start kernel: {e}")
            kernel_id = None
    else:
        kernel_id = None

    # Return notebook info
    return {
        "path": notebook_path,
        "name": os.path.basename(notebook_path),
        "message": message,
        "exists": notebook_exists,
        "kernel_name": kernel_name,
        "kernel_id": kernel_id,
    }
