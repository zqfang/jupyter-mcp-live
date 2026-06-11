"""Utilities for code execution in Jupyter kernels."""

import asyncio
import logging
from queue import Empty
from typing import Any

from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

logger = logging.getLogger(__name__)


async def wait_for_execute_reply(
    client: Any, msg_id: str, deadline: float
) -> int | None:
    """Wait for the execute_reply matching msg_id on the shell channel.

    Stale shell messages (e.g., replies to a previous execution that timed
    out) are drained and skipped rather than aborting, so a single leftover
    message does not lose the execution_count.

    Args:
        client: Kernel client with started channels
        msg_id: Parent message ID of our execute request
        deadline: Absolute event-loop time after which we give up

    Returns:
        execution_count from the matching reply, or None on timeout/error
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
        except Exception as e:
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
        if reply_content.get("status") == "error":
            logger.warning("execute_reply indicates error status")
        execution_count = reply_content.get("execution_count")
        logger.debug(f"Captured execution_count from shell reply: {execution_count}")
        return execution_count


async def collect_outputs(
    client: Any, msg_id: str, execution_count: int | None, deadline: float
) -> tuple[list[dict], str, str | None]:
    """Collect outputs from iopub channel until the kernel reports idle.

    Args:
        client: Kernel client
        msg_id: Message ID to match
        execution_count: Execution count from shell reply (for validation)
        deadline: Absolute event-loop time after which collection times out

    Returns:
        Tuple of (outputs, status, error_msg)
    """
    outputs = []
    status = "ok"
    error_msg = None

    loop = asyncio.get_running_loop()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            status = "error"
            error_msg = "Execution timed out while collecting outputs"
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
        if msg_type == "execute_result":
            # Validate execution_count matches
            result_exec_count = content.get("execution_count")
            if result_exec_count != execution_count:
                logger.warning(
                    f"execution_count mismatch: shell={execution_count}, "
                    f"execute_result={result_exec_count}"
                )

            outputs.append(
                {
                    "output_type": "execute_result",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {}),
                    "execution_count": execution_count,  # Use from shell reply
                }
            )

        elif msg_type == "stream":
            outputs.append(
                {
                    "output_type": "stream",
                    "name": content.get("name", "stdout"),
                    "text": content.get("text", ""),
                }
            )

        elif msg_type == "display_data":
            outputs.append(
                {
                    "output_type": "display_data",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {}),
                }
            )

        elif msg_type == "error":
            status = "error"
            error_msg = "\n".join(content.get("traceback", []))
            outputs.append(
                {
                    "output_type": "error",
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": content.get("traceback", []),
                }
            )

        elif msg_type == "status" and content.get("execution_state") == "idle":
            # Execution complete
            break

    return outputs, status, error_msg


async def execute_code_on_kernel(
    client: Any, code: str, kernel_id: str, timeout: int
) -> dict[str, Any]:
    """Execute code on kernel and collect results.

    The shell-reply wait and iopub output collection share a single deadline,
    so a hung kernel blocks for at most `timeout` seconds total (not 2x).

    Args:
        client: Kernel client with started channels
        code: Code to execute
        kernel_id: Kernel ID for logging
        timeout: Execution timeout in seconds (total budget)

    Returns:
        dict with execution_count, outputs, status, and optional error
    """
    # Execute the code
    logger.info(f"Executing code in kernel {kernel_id}")
    msg_id = client.execute(code)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    # Get execution_count from execute_reply on shell channel.  The reply
    # arrives when execution finishes, so iopub output is buffered by then.
    execution_count = await wait_for_execute_reply(client, msg_id, deadline)

    # Collect outputs from iopub channel.  Allow a small grace period past
    # the shared deadline so already-buffered output is still drained when
    # the shell wait consumed the entire budget.
    iopub_deadline = max(deadline, loop.time() + 5)
    outputs, status, error_msg = await collect_outputs(
        client, msg_id, execution_count, iopub_deadline
    )

    logger.info(f"Execution complete: status={status}, outputs={len(outputs)}")

    result = {"execution_count": execution_count, "outputs": outputs, "status": status}

    if error_msg:
        result["error"] = error_msg

    return result
