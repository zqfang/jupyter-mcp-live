"""Utilities for code execution in Jupyter kernels."""

import asyncio
import logging
from typing import Any

from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

logger = logging.getLogger(__name__)


async def validate_execution_reply(shell_reply: dict, msg_id: str) -> int | None:
    """Validate and extract execution_count from shell channel execute_reply.

    The execute_reply message ALWAYS contains execution_count, whereas
    execute_result on iopub only exists when code produces a result.

    Args:
        shell_reply: The shell reply message
        msg_id: Expected parent message ID

    Returns:
        execution_count if valid, None otherwise
    """
    # Validate this is the reply to our execution request
    if (
        shell_reply.get("parent_header", {}).get("msg_id") == msg_id
        and shell_reply.get("header", {}).get("msg_type") == "execute_reply"
    ):
        reply_content = shell_reply.get("content", {})
        execution_count = reply_content.get("execution_count")
        logger.debug(f"Captured execution_count from shell reply: {execution_count}")

        # Check execution status
        if reply_content.get("status") == "error":
            logger.warning("Execute_reply indicates error status")

        return execution_count
    logger.warning(
        f"Shell reply validation failed - "
        f"msg_type={shell_reply.get('header', {}).get('msg_type')}, "
        f"expected parent_msg_id={msg_id}, "
        f"got={shell_reply.get('parent_header', {}).get('msg_id')}"
    )
    return None


async def collect_outputs(
    client: Any, msg_id: str, execution_count: int | None, timeout: int
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
            # Directly await the async method
            msg = await asyncio.wait_for(client.get_iopub_msg(timeout=0.5), timeout=1)

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

            elif msg_type == "status":
                if content.get("execution_state") == "idle":
                    # Execution complete
                    break

        except asyncio.TimeoutError:
            # No message received in 1 second, check if we should continue
            await asyncio.sleep(EXECUTION_CONFIG.iopub_msg_rate_limit)
            continue

    return outputs, status, error_msg


async def execute_code_on_kernel(
    client: Any, code: str, kernel_id: str, timeout: int
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
            client.get_shell_msg(timeout=timeout), timeout=timeout + 1
        )
        execution_count = await validate_execution_reply(shell_reply, msg_id)
    except asyncio.TimeoutError:
        logger.error(
            f"Timeout waiting for execute_reply on shell channel after {timeout}s"
        )
    except Exception as e:
        logger.error(f"Error reading execute_reply from shell channel: {e}")

    # Collect outputs from iopub channel
    outputs, status, error_msg = await collect_outputs(
        client, msg_id, execution_count, timeout
    )

    logger.info(f"Execution complete: status={status}, outputs={len(outputs)}")

    result = {"execution_count": execution_count, "outputs": outputs, "status": status}

    if error_msg:
        result["error"] = error_msg

    return result
