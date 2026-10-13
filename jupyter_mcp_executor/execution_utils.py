"""Utilities for code execution in Jupyter kernels."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from queue import Empty
from typing import Any

from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

logger = logging.getLogger(__name__)

# Async callback fired once per collected output (live streaming hook)
OutputCallback = Callable[[dict[str, Any]], Awaitable[None]]
# Best-effort kernel interrupt (SIGINT to the running cell)
InterruptFn = Callable[[], Awaitable[None]]

# Error text collect_outputs reports when its deadline passes before idle
COLLECT_TIMEOUT_MSG = "Execution timed out while collecting outputs"
# Extra time the iopub collector gets past the execution deadline, so output
# the kernel already sent is still drained when the shell wait times out.
IOPUB_DRAIN_GRACE_SECS = 5

# Strong references to cleanup tasks started on cancellation, so they are not
# garbage-collected before they finish.
_CLEANUP_TASKS: set[asyncio.Task[None]] = set()


async def wait_for_execute_reply(
    client: Any, msg_id: str, deadline: float
) -> dict[str, Any] | None:
    """Wait for the execute_reply matching msg_id on the shell channel.

    Stale shell messages (e.g., replies to a previous execution that timed
    out) are drained and skipped rather than aborting, so a single leftover
    message does not lose the execution_count.

    Args:
        client: Kernel client with started channels
        msg_id: Parent message ID of our execute request
        deadline: Absolute event-loop time after which we give up

    Returns:
        The matching reply's content (status, execution_count, ...), or None
        on timeout/error
    """
    loop = asyncio.get_running_loop()

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            logger.error("Timeout waiting for execute_reply on shell channel")
            return None

        try:
            # jupyter_client raises queue.Empty when the channel poll times
            # out; asyncio.TimeoutError covers wrappers/mocks that use it.
            shell_reply = await client.get_shell_msg(timeout=remaining)
        except (Empty, TimeoutError):
            logger.error("Timeout waiting for execute_reply on shell channel")
            return None
        except Exception as e:  # noqa: BLE001 - treated like a missing reply
            logger.error(f"Error reading execute_reply from shell channel: {e}")
            return None

        header = shell_reply.get("header", {})
        parent_msg_id = shell_reply.get("parent_header", {}).get("msg_id")
        if parent_msg_id != msg_id or header.get("msg_type") != "execute_reply":
            logger.debug(
                f"Draining stale shell message "
                f"(msg_type={header.get('msg_type')}, parent={parent_msg_id})"
            )
            continue

        reply_content = shell_reply.get("content", {})
        if reply_content.get("status") in ("error", "aborted"):
            logger.warning(
                f"execute_reply indicates {reply_content.get('status')} status"
            )
        logger.debug(
            "Captured execution_count from shell reply: "
            f"{reply_content.get('execution_count')}"
        )
        return reply_content


async def collect_outputs(
    client: Any,
    msg_id: str,
    execution_count: int | None,
    deadline: float | Callable[[], float],
    on_output: OutputCallback | None = None,
) -> tuple[list[dict], str, str | None]:
    """Collect outputs from iopub channel until the kernel reports idle.

    Args:
        client: Kernel client
        msg_id: Message ID to match
        execution_count: Execution count from shell reply (for validation),
            or None when collecting concurrently with the shell wait —
            execute_result outputs then carry their own iopub-reported count
        deadline: Absolute event-loop time after which collection times out,
            or a function returning it (re-read every poll, so a caller can
            extend it, e.g. to drain output after interrupting the kernel)
        on_output: Optional async callback fired once per collected output as
            it arrives (live streaming). Callback errors are logged and
            disable further callbacks; they never abort execution.

    Returns:
        Tuple of (outputs, status, error_msg)
    """
    outputs = []
    status = "ok"
    error_msg = None

    loop = asyncio.get_running_loop()
    while True:
        current_deadline = deadline() if callable(deadline) else deadline
        remaining = current_deadline - loop.time()
        if remaining <= 0:
            status = "error"
            error_msg = COLLECT_TIMEOUT_MSG
            break

        try:
            poll = min(EXECUTION_CONFIG.iopub_poll_interval, remaining)
            msg = await client.get_iopub_msg(timeout=poll)
        except (Empty, TimeoutError):
            # No message ready yet — keep polling until the deadline.
            await asyncio.sleep(EXECUTION_CONFIG.iopub_msg_rate_limit)
            continue

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
            error_msg = f"Malformed kernel message: {e!s}"
            break

        # Check if this is a reply to our execute request
        if msg["parent_header"].get("msg_id") != msg_id:
            continue

        # Handle different message types
        output: dict[str, Any] | None = None

        if msg_type == "execute_result":
            # Validate execution_count when the caller already knows it.
            # (In concurrent collection it is None here; execute_code_on_kernel
            # reconciles against the shell reply afterwards.)
            result_exec_count = content.get("execution_count")
            if execution_count is not None and result_exec_count != execution_count:
                logger.warning(
                    f"execution_count mismatch: shell={execution_count}, "
                    f"execute_result={result_exec_count}"
                )

            output = {
                "output_type": "execute_result",
                "data": content.get("data", {}),
                "metadata": content.get("metadata", {}),
                "execution_count": execution_count or result_exec_count,
            }

        elif msg_type == "stream":
            output = {
                "output_type": "stream",
                "name": content.get("name", "stdout"),
                "text": content.get("text", ""),
            }

        elif msg_type == "display_data":
            output = {
                "output_type": "display_data",
                "data": content.get("data", {}),
                "metadata": content.get("metadata", {}),
            }

        elif msg_type == "error":
            status = "error"
            error_msg = "\n".join(content.get("traceback", []))
            output = {
                "output_type": "error",
                "ename": content.get("ename", ""),
                "evalue": content.get("evalue", ""),
                "traceback": content.get("traceback", []),
            }

        elif msg_type == "status" and content.get("execution_state") == "idle":
            # Execution complete
            break

        if output is not None:
            outputs.append(output)
            if on_output is not None:
                try:
                    await on_output(output)
                except Exception:
                    logger.warning(
                        "Output streaming callback failed; live updates "
                        "disabled for the rest of this execution",
                        exc_info=True,
                    )
                    on_output = None

    return outputs, status, error_msg


async def execute_code_on_kernel(
    client: Any,
    code: str,
    kernel_id: str,
    timeout: float,
    on_output: OutputCallback | None = None,
    interrupt: InterruptFn | None = None,
    interrupt_grace: float | None = None,
    cancel_cleanup: list[asyncio.Task[None]] | None = None,
) -> dict[str, Any]:
    """Execute code on kernel and collect results.

    The shell-reply wait and iopub output collection share a single deadline,
    so a hung kernel blocks for at most `timeout` seconds total (not 2x).

    When ``interrupt`` is given, the kernel is interrupted instead of being
    left busy:

    - on timeout, then output is drained until the kernel reports idle
      (bounded by ``interrupt_grace``), so the next execution doesn't queue
      behind a runaway cell;
    - when this coroutine is cancelled (e.g. the MCP request was cancelled).
      Cancellation is not delayed: a background cleanup task interrupts the
      kernel and drains it to idle (bounded by ``interrupt_grace``), and is
      appended to ``cancel_cleanup`` so the caller can keep the kernel
      reserved until it finishes. Otherwise the next execution could reach the
      kernel while the interrupted cell is still ending, and ipykernel aborts
      requests queued behind a failing cell.

    Both inner tasks are always finished (by the cleanup task after a
    cancellation), so no collector outlives the execution it belongs to.

    Args:
        client: Kernel client with started channels
        code: Code to execute
        kernel_id: Kernel ID for logging
        timeout: Execution timeout in seconds (total budget)
        on_output: Optional async callback fired per collected output
            (live streaming); see collect_outputs
        interrupt: Optional best-effort kernel interrupt
        interrupt_grace: Seconds to wait for idle after an interrupt;
            defaults to ``EXECUTION_CONFIG.interrupt_grace_secs``
        cancel_cleanup: Receives the background cleanup task started when
            this execution is cancelled (only if ``interrupt`` is given)

    Returns:
        dict with execution_count, outputs, status, and optional error
    """
    # Execute the code
    logger.info(f"Executing code in kernel {kernel_id}")
    msg_id = client.execute(code)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    iopub_deadline = deadline + IOPUB_DRAIN_GRACE_SECS
    grace = (
        EXECUTION_CONFIG.interrupt_grace_secs
        if interrupt_grace is None
        else interrupt_grace
    )

    # Wait for the execute_reply (shell) and collect outputs (iopub)
    # CONCURRENTLY.  The execute_reply only arrives when execution finishes,
    # so collecting sequentially would buffer all output until the end —
    # outputs must be consumed while the code is still running for the
    # on_output streaming callback to deliver live updates.  The iopub task
    # gets a small grace past the shared deadline so already-buffered output
    # is still drained when the kernel hits the timeout; its deadline is
    # re-read every poll so a timeout interrupt can extend it.
    shell_task = asyncio.create_task(wait_for_execute_reply(client, msg_id, deadline))
    iopub_task = asyncio.create_task(
        collect_outputs(
            client, msg_id, None, lambda: iopub_deadline, on_output=on_output
        )
    )
    interrupted = False
    finish_inline = True

    async def cleanup_after_cancel() -> None:
        """Interrupt, drain to idle within the grace, then end both tasks."""
        nonlocal iopub_deadline
        try:
            await _interrupt_kernel(interrupt, kernel_id)
            iopub_deadline = loop.time() + grace
            if not iopub_task.done():
                await asyncio.wait({iopub_task})
        finally:
            await _finish_tasks(shell_task, iopub_task)

    try:
        reply = await shell_task
        execution_count = reply.get("execution_count") if reply else None
        if reply is None and interrupt and loop.time() >= deadline:
            logger.warning(
                f"Execution on kernel {kernel_id} timed out after {timeout}s; "
                "interrupting the kernel"
            )
            await _interrupt_kernel(interrupt, kernel_id)
            interrupted = True
            # From here only the interrupt grace bounds the drain.
            iopub_deadline = loop.time() + grace
        outputs, status, error_msg = await iopub_task
    except asyncio.CancelledError:
        if interrupt is not None:
            # Don't wait here: the caller may be cancelled again at every
            # await (anyio cancel scopes). The cleanup runs in its own task.
            logger.info(f"Execution on kernel {kernel_id} cancelled; interrupting")
            cleanup = asyncio.create_task(cleanup_after_cancel())
            _CLEANUP_TASKS.add(cleanup)
            cleanup.add_done_callback(_CLEANUP_TASKS.discard)
            if cancel_cleanup is not None:
                cancel_cleanup.append(cleanup)
            finish_inline = False
        raise
    finally:
        if finish_inline:
            await _finish_tasks(shell_task, iopub_task)

    if reply is not None and reply.get("status") == "aborted":
        status = "error"
        error_msg = (
            "Execution was aborted by the kernel: it was queued behind an "
            "execution that failed or was interrupted. Re-run the cell."
        )

    if interrupted:
        status = "error"
        if error_msg == COLLECT_TIMEOUT_MSG:
            error_msg = (
                f"Execution timed out after {timeout}s; the interrupt did not "
                f"stop it within {grace}s — the kernel is still busy; use "
                "restart_notebook_kernel"
            )
        else:
            error_msg = (
                f"Execution timed out after {timeout}s; the kernel was interrupted"
            )

    # execute_result messages were collected before the shell reply supplied
    # the authoritative execution_count; reconcile them now (the kernel
    # reports the same count on both channels, so mismatches are anomalies).
    if execution_count is not None:
        for out in outputs:
            if (
                out.get("output_type") == "execute_result"
                and out.get("execution_count") != execution_count
            ):
                logger.warning(
                    f"execution_count mismatch: shell={execution_count}, "
                    f"execute_result={out.get('execution_count')}"
                )
                out["execution_count"] = execution_count

    logger.info(f"Execution complete: status={status}, outputs={len(outputs)}")

    result = {"execution_count": execution_count, "outputs": outputs, "status": status}

    if error_msg:
        result["error"] = error_msg

    return result


async def _interrupt_kernel(interrupt: InterruptFn, kernel_id: str) -> bool:
    """Interrupt the kernel; log and swallow failures."""
    try:
        await interrupt()
    except Exception:  # best-effort; the caller reports the outcome
        logger.warning(f"Could not interrupt kernel {kernel_id}", exc_info=True)
        return False
    return True


async def _finish_tasks(*tasks: asyncio.Task[Any]) -> None:
    """Cancel tasks that are still running and wait until they have ended."""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
