"""Execution engine for running code in Jupyter kernels."""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from jupyter_core.utils import ensure_async

from jupyter_mcp_executor.execution_progress import ExecutionProgress
from jupyter_mcp_executor.execution_utils import OutputCallback, execute_code_on_kernel
from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.kernel_utils import is_kernel_alive
from jupyter_mcp_executor.notebook_adapter import NotebookAdapter, get_notebook_adapter
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG
from jupyter_mcp_executor.notebook_models import ExecutionResult
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import get_server_context

logger = logging.getLogger(__name__)


def _combine_callbacks(*callbacks: OutputCallback | None) -> OutputCallback | None:
    """Fan one output out to several callbacks (each handles its own errors)."""
    active = [callback for callback in callbacks if callback is not None]
    if len(active) <= 1:
        return active[0] if active else None

    async def combined(output: dict[str, Any]) -> None:
        for callback in active:
            await callback(output)

    return combined


async def _keep_room_alive(adapter: NotebookAdapter) -> None:
    """Keep the adapter's YDoc room alive until cancelled.

    A silent cell makes no adapter calls, so without this the room could be
    deleted (the browser left) while the cell runs. Errors only stop it.
    """
    try:
        while True:
            adapter.keep_alive()
            await asyncio.sleep(EXECUTION_CONFIG.room_keepalive_interval)
    except Exception:
        logger.debug("Keeping the YDoc room alive failed; stopped", exc_info=True)


class _CellOutputStreamer:
    """Mirrors outputs into the notebook as the kernel emits them.

    Used only for collaborative (YDoc) adapters so JupyterLab renders output
    live during long-running executions.  The cell is re-located by ID before
    every write so streamed output follows the cell if positions shift.  Any
    failure disables streaming for the rest of the run; the final batch write
    in execute_and_update_cell then reconciles the cell.
    """

    def __init__(self, adapter: NotebookAdapter, cell_id: str, position: int):
        self._adapter = adapter
        self._cell_id = cell_id
        self._position = position
        self._generation = getattr(adapter, "generation", 0)
        self.streamed_ok = True

    @property
    def complete(self) -> bool:
        """Whether every output is in the cell, so only the count is left.

        Not if streaming failed, or if the adapter moved to a recreated YDoc
        room meanwhile: the old room's last outputs may never have reached it.
        """
        return self.streamed_ok and (
            getattr(self._adapter, "generation", 0) == self._generation
        )

    async def _locate_cell(self) -> int:
        """Resolve the cell's current position by ID (positions can shift)."""
        found = await self._adapter.find_cell_by_id(self._cell_id)
        if found is None:
            msg = f"Cell {self._cell_id} no longer exists"
            raise LookupError(msg)
        self._position = found[1]
        return self._position

    async def clear_outputs(self) -> None:
        """Clear stale outputs at execution start."""
        try:
            position = await self._locate_cell()
            await self._adapter.clear_cell_outputs(position)
        except Exception as e:  # noqa: BLE001 - streaming must never abort execution
            logger.warning(f"Could not clear outputs before execution: {e}")
            self.streamed_ok = False

    async def on_output(self, output: dict[str, Any]) -> None:
        """Append one output to the cell as it arrives from the kernel."""
        if not self.streamed_ok:
            return
        try:
            position = await self._locate_cell()
            await self._adapter.append_cell_output(position, output)
        except Exception as e:  # noqa: BLE001 - streaming must never abort execution
            logger.warning(f"Live output streaming disabled for this run: {e}")
            self.streamed_ok = False


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
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _release_after(
        self, lock: asyncio.Lock, tasks: list[asyncio.Task[None]]
    ) -> None:
        """Release ``lock`` once ``tasks`` (cancellation cleanup) are done."""

        async def release() -> None:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                lock.release()

        task = asyncio.create_task(release())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def forget_kernel_client(self, kernel_id: str) -> None:
        """Drop the cached client so the next execution reconnects.

        Call after the kernel was restarted outside this engine.
        """
        self._drop_client(kernel_id)

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
        on_output: OutputCallback | None = None,
        progress_label: str | None = None,
    ) -> ExecutionResult:
        """Execute code in a Jupyter kernel.

        Args:
            code: The code to execute
            kernel_id: The kernel to run on, resolved by the caller (see
                KernelManager.get_kernel_for_notebook). None is an error: a
                bare kernel started here would have no Jupyter session, so
                JupyterLab and later lookups could not find it.
            timeout: Maximum time in seconds to wait for execution
            on_output: Optional async callback fired per output as it arrives
                (live streaming into the notebook)
            progress_label: Names the execution in MCP progress notifications
                (e.g. "analysis.ipynb cell 3"); progress is sent only while
                serving an MCP request.

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
        if kernel_id is None:
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id="",
                error="No kernel to run the code on; call setup_notebook first",
            )

        # Get kernel handle (also validates the kernel is still alive).
        # is_kernel_alive, not `except KeyError`: jupyter_server raises
        # tornado.web.HTTPError(404) for a dead kernel, which a KeyError guard
        # lets escape to the caller as an opaque "HTTP 404: Kernel does not exist".
        if is_kernel_alive(serverapp, kernel_id):
            kernel = kernel_manager.get_kernel(kernel_id)
        else:
            self._drop_client(kernel_id)
            return ExecutionResult(
                execution_count=None,
                outputs=[],
                status="error",
                kernel_id=kernel_id,
                error=f"Kernel {kernel_id} not found",
            )

        # MCP progress notifications for the current request (None outside
        # one). Started before taking the kernel lock so a queued execution
        # reports "waiting for kernel" instead of going silent.
        progress = ExecutionProgress.from_request(progress_label or "code")
        callback = _combine_callbacks(
            on_output, progress.on_output if progress else None
        )

        # Serialize executions per kernel: the cached client's channels must
        # only be consumed by one coroutine at a time.
        lock = self._kernel_locks.setdefault(kernel_id, asyncio.Lock())
        cleanup: list[asyncio.Task[None]] = []
        async with progress or contextlib.nullcontext():
            await lock.acquire()
            handed_off = False
            try:
                if progress is not None:
                    progress.mark_running()
                client = await self._get_client(kernel, kernel_id)
                result_dict = await execute_code_on_kernel(
                    client,
                    code,
                    kernel_id,
                    timeout,
                    on_output=callback,
                    interrupt=lambda: ensure_async(
                        kernel_manager.interrupt_kernel(kernel_id)
                    ),
                    cancel_cleanup=cleanup,
                )
            except asyncio.CancelledError:
                # Keep the kernel reserved until the interrupted cell has
                # ended; the cleanup task releases the lock when it finishes.
                if cleanup:
                    handed_off = True
                    self._release_after(lock, cleanup)
                raise
            except Exception:
                # Connection-level failure: drop the cached client so the
                # next execution reconnects from scratch.
                self._drop_client(kernel_id)
                raise
            finally:
                if not handed_off:
                    lock.release()

        return ExecutionResult(
            execution_count=result_dict.get("execution_count"),
            outputs=result_dict.get("outputs", []),
            status=result_dict.get("status", "ok"),
            kernel_id=kernel_id,
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
        or in disk file for closed notebooks.  For open notebooks (when cell_id
        is given) outputs are additionally streamed into the cell live, one
        iopub message at a time, so JupyterLab renders progress during
        long-running executions instead of everything appearing at the end.

        Args:
            adapter: Notebook adapter (YDoc or nbformat)
            cell_position: Position of cell to update
            code: Code to execute
            kernel_id: Optional kernel ID. If None, the notebook's existing
                kernel is reused (tracked kernel or running Jupyter session);
                a new session-backed kernel is started only if none exists,
                and KernelStartError is raised if that fails.
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

        # Live-stream outputs for open notebooks: clear stale outputs now and
        # mirror each output into the YDoc as the kernel emits it, so
        # JupyterLab renders progress during long-running cells.  Closed
        # notebooks (NbformatAdapter) skip this — nobody is watching the file,
        # and per-output disk writes would be pure overhead.
        streamer = None
        if cell_id and adapter.is_collaborative:
            streamer = _CellOutputStreamer(adapter, cell_id, cell_position)
            await streamer.clear_outputs()

        # Execute code, keeping an open notebook's room alive meanwhile.  The
        # task is only cancelled, never awaited, so a cancelled call does not
        # wait on it (anyio re-delivers cancellation at every await).
        keepalive = asyncio.create_task(_keep_room_alive(adapter)) if streamer else None
        try:
            exec_result = await self.execute_cell_code(
                code,
                kernel_id=kernel_id,
                timeout=timeout,
                on_output=streamer.on_output if streamer else None,
                progress_label=(
                    f"{Path(adapter.notebook_path).name} cell {cell_position}"
                ),
            )
        finally:
            if keepalive is not None:
                keepalive.cancel()

        if started_new:
            exec_result = exec_result.model_copy(update={"started_new_kernel": True})

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
        if streamer is not None and streamer.complete:
            # Outputs already live in the cell via streaming; only record the
            # execution_count.  Skipping the clear+rewrite of identical
            # content avoids a re-render flash of plots/images in JupyterLab.
            await adapter.update_cell_outputs(
                update_position, exec_result.execution_count, None
            )
        else:
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
