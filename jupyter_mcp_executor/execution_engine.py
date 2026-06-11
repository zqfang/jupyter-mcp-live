"""Execution engine for running code in Jupyter kernels."""

import asyncio
import contextlib
import logging
from typing import Any

from jupyter_mcp_executor.execution_utils import execute_code_on_kernel
from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.notebook_adapter import NotebookAdapter, get_notebook_adapter
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG
from jupyter_mcp_executor.notebook_models import ExecutionResult
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import get_server_context

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Engine for executing code and updating cell outputs.

    Handles:
    - Code execution in kernels
    - Output collection
    - Cell output updates (via adapters)
    - Persistence to disk for collaborative mode

    Kernel clients are cached per kernel and executions on the same kernel
    are serialized with a per-kernel lock: a shared client's iopub channel
    must not be consumed by two coroutines concurrently (they would steal
    each other's messages), and the kernel executes serially anyway.
    """

    def __init__(self, kernel_service: KernelManager | None = None):
        """Initialize execution engine.

        Args:
            kernel_service: KernelManager used to resolve/reuse kernels for
                notebooks. A new instance is created if not provided (it is
                stateless apart from the shared kernel tracker singleton).
        """
        self.kernel_tracker = get_kernel_tracker()
        self.kernel_service = kernel_service or KernelManager()
        self._clients: dict[str, Any] = {}
        self._kernel_locks: dict[str, asyncio.Lock] = {}

    def _drop_client(self, kernel_id: str) -> None:
        """Discard the cached client for a kernel (e.g., kernel died)."""
        client = self._clients.pop(kernel_id, None)
        self._kernel_locks.pop(kernel_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                client.stop_channels()

    async def _get_client(self, kernel: Any, kernel_id: str) -> Any:
        """Get (or create and cache) a ready client for a kernel.

        Reusing the client avoids per-execution ZMQ channel setup and the
        kernel_info round-trip of wait_for_ready.
        """
        client = self._clients.get(kernel_id)
        if client is not None:
            return client

        client = kernel.client()
        client.start_channels()
        try:
            await client.wait_for_ready(timeout=EXECUTION_CONFIG.kernel_ready_timeout)
        except Exception:
            with contextlib.suppress(Exception):
                client.stop_channels()
            raise
        self._clients[kernel_id] = client
        return client

    async def execute_cell_code(
        self,
        code: str,
        kernel_id: str | None = None,
        timeout: int | None = None,
        kernel_name: str = "python3",
    ) -> ExecutionResult:
        """Execute code in a Jupyter kernel.

        Args:
            code: The code to execute
            kernel_id: Optional kernel ID to use. If None, starts a new kernel
                (last-resort path; callers normally resolve the kernel first).
            timeout: Maximum time in seconds to wait for execution
            kernel_name: Kernel name to use when starting new kernel

        Returns:
            ExecutionResult with outputs and status
        """
        if timeout is None:
            timeout = EXECUTION_CONFIG.default_timeout

        serverapp = get_server_context()
        if serverapp is None:
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id="",
                error="Server context not available",
            )

        kernel_manager = serverapp.kernel_manager
        started_new_kernel = False

        # Get or create kernel
        if kernel_id is None:
            logger.info(f"Starting new {kernel_name} kernel")
            kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)
            started_new_kernel = True
            logger.info(f"Started kernel {kernel_id}")

        # Get kernel handle (also validates the kernel is still alive)
        try:
            kernel = kernel_manager.get_kernel(kernel_id)
        except KeyError:
            self._drop_client(kernel_id)
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id=kernel_id,
                error=f"Kernel {kernel_id} not found",
            )

        # Serialize executions per kernel: the cached client's channels must
        # only be consumed by one coroutine at a time.
        lock = self._kernel_locks.setdefault(kernel_id, asyncio.Lock())
        async with lock:
            try:
                client = await self._get_client(kernel, kernel_id)
                result_dict = await execute_code_on_kernel(
                    client, code, kernel_id, timeout
                )
            except Exception:
                # Connection-level failure: drop the cached client so the
                # next execution reconnects from scratch.
                self._drop_client(kernel_id)
                raise

        return ExecutionResult(
            execution_count=result_dict.get("execution_count"),
            outputs=result_dict.get("outputs", []),
            status=result_dict.get("status", "ok"),
            kernel_id=kernel_id,
            started_new_kernel=started_new_kernel,
            error=result_dict.get("error"),
        )

    async def execute_and_update_cell(
        self,
        adapter: NotebookAdapter,
        cell_position: int,
        code: str,
        kernel_id: str | None = None,
        timeout: int | None = None,
        cell_id: str | None = None,
    ) -> ExecutionResult:
        """Execute code and update cell with results in one atomic operation.

        This method is YDoc-aware: it updates outputs in YDoc for open notebooks,
        or in disk file for closed notebooks.

        Args:
            adapter: Notebook adapter (YDoc or nbformat)
            cell_position: Position of cell to update
            code: Code to execute
            kernel_id: Optional kernel ID. If None, the notebook's existing
                kernel is reused (tracked kernel or running Jupyter session);
                a new session-backed kernel is started only if none exists.
            timeout: Execution timeout
            cell_id: UUID of the target cell. When given, the cell is
                re-located by ID after execution so outputs land in the right
                cell even if other cells were added/deleted (e.g., by the
                user in JupyterLab) while the code was running.

        Returns:
            ExecutionResult Pydantic model with execution details
        """
        if timeout is None:
            timeout = EXECUTION_CONFIG.default_timeout

        # Resolve the notebook's kernel: reuse tracked/session kernel, or start
        # a session-backed one. Never silently fork a bare untracked kernel —
        # that loses user state and is invisible to later session lookups.
        started_new = False
        if kernel_id is None:
            kernel_id, started_new = await self.kernel_service.get_kernel_for_notebook(
                adapter.notebook_path
            )

        # Execute code
        exec_result = await self.execute_cell_code(
            code, kernel_id=kernel_id, timeout=timeout
        )

        if started_new and not exec_result.started_new_kernel:
            exec_result = exec_result.model_copy(update={"started_new_kernel": True})

        # Track kernel if new
        if exec_result.started_new_kernel:
            self.kernel_tracker.track(adapter.notebook_path, exec_result.kernel_id)

        # Re-locate the cell by ID: positions can shift during a long
        # execution if cells are added/deleted concurrently in JupyterLab.
        update_position = cell_position
        if cell_id:
            found = await adapter.find_cell_by_id(cell_id)
            if found is not None:
                update_position = found[1]
                if update_position != cell_position:
                    logger.info(
                        f"Cell {cell_id} moved from position {cell_position} to "
                        f"{update_position} during execution"
                    )
            else:
                logger.warning(
                    f"Cell {cell_id} was deleted during execution; "
                    f"skipping output update"
                )
                return exec_result

        # Update cell outputs via adapter.
        # - NbformatAdapter (closed notebook): writes outputs directly to disk.
        # - YDocAdapter (open notebook): writes outputs to YDoc only; JupyterLab
        #   autosave handles disk persistence.  A direct nbformat.write() here
        #   would trigger JupyterLab's external-file-change detector and cause
        #   the notebook viewport to scroll back to the top on every execution.
        await adapter.update_cell_outputs(
            update_position, exec_result.execution_count, exec_result.outputs
        )

        # Return ExecutionResult (caller can convert to dict if needed)
        return exec_result

    async def execute_cell_by_position(
        self, notebook_path: str, position: int, timeout: int | None = None
    ) -> ExecutionResult:
        """Execute an existing cell by position.

        Args:
            notebook_path: Path to notebook
            position: Cell position
            timeout: Execution timeout

        Returns:
            ExecutionResult Pydantic model with execution details
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Read cell code
        cell = await adapter.read_cell(position)
        if cell.cell_type != "code":
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id="",
                error=f"Cell at position {position} is not a code cell",
            )

        code = cell.get_source_as_string()

        # Execute and update; kernel is resolved per-notebook (reuse before
        # create) and the cell is re-located by ID after execution.
        return await self.execute_and_update_cell(
            adapter, position, code, timeout=timeout, cell_id=cell.cell_id
        )
