"""Execution engine for running code in Jupyter kernels."""

import asyncio
import logging
from typing import Any

from jupyter_mcp_executor.execution_utils import execute_code_on_kernel
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.notebook_adapter import NotebookAdapter, get_notebook_adapter
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG
from jupyter_mcp_executor.notebook_io import NotebookContext
from jupyter_mcp_executor.notebook_models import ExecutionResult
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
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
    """

    def __init__(self):
        """Initialize execution engine."""
        self.kernel_tracker = get_kernel_tracker()

    async def execute_cell_code(
        self,
        code: str,
        kernel_id: str | None = None,
        timeout: int | None = None,
        kernel_name: str = "python3"
    ) -> ExecutionResult:
        """Execute code in a Jupyter kernel.

        Args:
            code: The code to execute
            kernel_id: Optional kernel ID to use. If None, starts a new kernel.
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
                error="Server context not available"
            )

        kernel_manager = serverapp.kernel_manager
        started_new_kernel = False

        # Get or create kernel
        if kernel_id is None:
            logger.info(f"Starting new {kernel_name} kernel")
            kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)
            started_new_kernel = True
            logger.info(f"Started kernel {kernel_id}")

            # Wait for kernel to be ready
            await asyncio.sleep(EXECUTION_CONFIG.kernel_startup_delay)

        # Get kernel client
        try:
            kernel = kernel_manager.get_kernel(kernel_id)
        except KeyError:
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id=kernel_id,
                error=f"Kernel {kernel_id} not found"
            )

        client = kernel.client()
        client.start_channels()

        try:
            # Wait for kernel ready
            await client.wait_for_ready(timeout=EXECUTION_CONFIG.kernel_ready_timeout)

            # Execute code
            result_dict = await execute_code_on_kernel(client, code, kernel_id, timeout)

            # Convert to ExecutionResult
            return ExecutionResult(
                execution_count=result_dict.get("execution_count"),
                outputs=result_dict.get("outputs", []),
                status=result_dict.get("status", "ok"),
                kernel_id=kernel_id,
                started_new_kernel=started_new_kernel,
                error=result_dict.get("error")
            )

        finally:
            client.stop_channels()

    async def execute_and_update_cell(
        self,
        adapter: NotebookAdapter,
        cell_position: int,
        code: str,
        kernel_id: str | None = None,
        timeout: int | None = None
    ) -> dict[str, Any]:
        """Execute code and update cell with results in one atomic operation.

        This method is YDoc-aware: it updates outputs in YDoc for open notebooks,
        or in disk file for closed notebooks.

        Args:
            adapter: Notebook adapter (YDoc or nbformat)
            cell_position: Position of cell to update
            code: Code to execute
            kernel_id: Optional kernel ID
            timeout: Execution timeout

        Returns:
            Filtered execution result dict for MCP response
        """
        if timeout is None:
            timeout = EXECUTION_CONFIG.default_timeout

        # Execute code
        exec_result = await self.execute_cell_code(
            code,
            kernel_id=kernel_id,
            timeout=timeout
        )

        # Track kernel if new
        if exec_result.started_new_kernel:
            self.kernel_tracker.track(adapter.notebook_path, exec_result.kernel_id)

        # Update cell outputs via adapter
        await adapter.update_cell_outputs(
            cell_position,
            exec_result.execution_count,
            exec_result.outputs
        )

        # If collaborative mode, also persist to disk
        if adapter.is_collaborative:
            logger.debug(f"Persisting cell {cell_position} outputs to disk")
            await self._persist_outputs_to_disk(
                adapter.notebook_path,
                cell_position,
                exec_result.execution_count,
                exec_result.outputs
            )

        # Return filtered result for MCP
        return exec_result.to_filtered_dict()

    async def execute_cell_by_position(
        self,
        notebook_path: str,
        position: int,
        timeout: int | None = None
    ) -> dict[str, Any]:
        """Execute an existing cell by position.

        Args:
            notebook_path: Path to notebook
            position: Cell position
            timeout: Execution timeout

        Returns:
            Filtered execution result dict
        """
        notebook_path = normalize_path(notebook_path)
        adapter = await get_notebook_adapter(notebook_path)

        # Read cell code
        cell = await adapter.read_cell(position)
        if cell.cell_type != "code":
            return {
                "error": f"Cell at position {position} is not a code cell",
                "status": "error"
            }

        code = cell.get_source_as_string()

        # Get tracked kernel for this notebook
        kernel_id = self.kernel_tracker.get(notebook_path)

        # Execute and update
        return await self.execute_and_update_cell(
            adapter,
            position,
            code,
            kernel_id=kernel_id,
            timeout=timeout
        )

    async def install_packages(
        self,
        notebook_path: str,
        package_names: str
    ) -> str:
        """Install Python packages in the notebook's kernel environment.

        Args:
            notebook_path: Path to notebook
            package_names: Space-separated package names

        Returns:
            Installation result message
        """
        notebook_path = normalize_path(notebook_path)

        # Get or start kernel
        kernel_id = self.kernel_tracker.get(notebook_path)
        if not kernel_id:
            logger.info("No kernel found, starting new kernel for package installation")
            result = await self.execute_cell_code(
                code="",  # Empty code to just start kernel
                kernel_id=None,
                timeout=10
            )
            kernel_id = result.kernel_id
            self.kernel_tracker.track(notebook_path, kernel_id)

        # Execute pip install command
        install_code = f"""
import sys
import subprocess

packages = "{package_names}"
result = subprocess.run(
    [sys.executable, "-m", "pip", "install"] + packages.split(),
    capture_output=True,
    text=True
)

if result.returncode == 0:
    print(f"Successfully installed: {{packages}}")
else:
    print(f"Installation failed:\\n{{result.stderr}}")
"""

        result = await self.execute_cell_code(
            code=install_code,
            kernel_id=kernel_id,
            timeout=300  # 5 minutes for package installation
        )

        if result.status == "error":
            return f"Package installation failed: {result.error}"

        # Extract output text
        output_text = ""
        for output in result.outputs:
            if output.get("output_type") == "stream":
                output_text += output.get("text", "")

        return output_text or f"Package installation completed for: {package_names}"

    async def _persist_outputs_to_disk(
        self,
        notebook_path: str,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]]
    ) -> None:
        """Persist cell outputs to disk (for collaborative mode).

        This ensures YDoc changes are also saved to the .ipynb file.

        Args:
            notebook_path: Path to notebook
            position: Cell position
            execution_count: Execution count
            outputs: Output list
        """
        try:
            with NotebookContext(notebook_path) as nb:
                if position < len(nb.cells):
                    cell = nb.cells[position]
                    if execution_count is not None:
                        cell.execution_count = execution_count
                    if outputs:
                        cell.outputs = convert_outputs_to_notebook_nodes(outputs)
                else:
                    logger.warning(f"Cell index {position} out of range in disk notebook")
        except Exception as e:
            logger.warning(f"Failed to persist outputs to disk: {e}")
