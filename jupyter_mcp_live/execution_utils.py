"""Utilities for code execution in Jupyter kernels."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from queue import Empty
from typing import Any

from jupyter_mcp_live.notebook_config import EXECUTION_CONFIG

logger = logging.getLogger(__name__)

# Async callback fired once per collected output (live streaming hook)
OutputCallback = Callable[[dict[str, Any]], Awaitable[None]]
# Async callback fired with the full output list after a clear or an update
ResetCallback = Callable[[list[dict[str, Any]]], Awaitable[None]]
# Best-effort kernel interrupt (SIGINT to the running cell)
InterruptFn = Callable[[], Awaitable[None]]

# Error text collect_outputs reports when its deadline passes before idle
COLLECT_TIMEOUT_MSG = "Execution timed out while collecting outputs"
# Extra time the iopub collector gets past the execution deadline, so output
# the kernel already sent is still drained when the shell wait times out.
IOPUB_DRAIN_GRACE_SECS = 5
# Once iopub reports the kernel idle, wait at most this long for the
# execute_reply before returning without it (a lost reply must not hold the
# call until the deadline, which can be hours away).
SHELL_REPLY_GRACE_SECS = 2

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
            # The poll can give up a few ms before ``remaining`` elapsed; loop
            # back so None is only returned once the deadline really passed
            # (callers treat that as a timeout and interrupt the kernel).
            continue
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


class _CellOutputs:
    """The outputs of one execution, kept as JupyterLab would show them.

    - Consecutive stream chunks of the same name are one output (each print
      is its own iopub message).
    - ``clear_output`` empties the list; with ``wait=True`` only when the next
      output arrives, so a redrawn frame replaces the previous one.
    - ``update_display_data`` updates the outputs shown with that display_id
      (displays of other cells are not tracked here).

    Live streaming gets ``on_output(chunk)`` for appends and
    ``on_reset(outputs)`` when the list changed otherwise. A failing callback
    disables both for the rest of the execution; it never aborts it.
    """

    def __init__(
        self, on_output: OutputCallback | None, on_reset: ResetCallback | None
    ) -> None:
        self._outputs: list[dict[str, Any]] = []
        # Text chunks of the last output while it is a merged stream; joined
        # when the list is read, so merging stays linear in the text size.
        self._tail_chunks: list[str] = []
        self._displays: dict[str, list[int]] = {}
        self._clear_pending = False
        self._on_output = on_output
        self._on_reset = on_reset
        self._live = True

    @property
    def outputs(self) -> list[dict[str, Any]]:
        """The outputs so far, merged stream text joined."""
        if len(self._tail_chunks) > 1:
            text = "".join(self._tail_chunks)
            self._outputs[-1] = {**self._outputs[-1], "text": text}
            self._tail_chunks = [text]
        return self._outputs

    async def add(self, output: dict[str, Any], display_id: str | None) -> None:
        if self._clear_pending:
            self._clear()
            self._append(output, display_id)
            await self._notify(self._on_reset, list(self.outputs))
            return
        self._append(output, display_id)
        await self._notify(self._on_output, output)

    async def clear(self, *, wait: bool) -> None:
        if wait:
            self._clear_pending = True
            return
        self._clear()
        await self._notify(self._on_reset, [])

    async def update(
        self, display_id: str, data: dict[str, Any], metadata: dict[str, Any]
    ) -> None:
        indexes = self._displays.get(display_id)
        if not indexes:
            logger.debug(f"Ignoring update of display {display_id} (not shown here)")
            return
        outputs = self.outputs
        for index in indexes:
            outputs[index] = {**outputs[index], "data": data, "metadata": metadata}
        await self._notify(self._on_reset, list(outputs))

    def _clear(self) -> None:
        self._outputs = []
        self._tail_chunks = []
        self._displays = {}
        self._clear_pending = False

    def _append(self, output: dict[str, Any], display_id: str | None) -> None:
        is_stream = output["output_type"] == "stream"
        last = self._outputs[-1] if self._outputs else None
        if (
            is_stream
            and last is not None
            and last["output_type"] == "stream"
            and last["name"] == output["name"]
        ):
            self._tail_chunks.append(output["text"])
            return
        outputs = self.outputs  # join the previous stream before moving on
        if display_id is not None:
            self._displays.setdefault(display_id, []).append(len(outputs))
        outputs.append(output)
        self._tail_chunks = [output["text"]] if is_stream else []

    async def _notify(
        self, callback: Callable[[Any], Awaitable[None]] | None, arg: Any
    ) -> None:
        if callback is None or not self._live:
            return
        try:
            await callback(arg)
        except Exception:
            logger.warning(
                "Output streaming callback failed; live updates disabled for "
                "the rest of this execution",
                exc_info=True,
            )
            self._live = False


async def collect_outputs(
    client: Any,
    msg_id: str,
    execution_count: int | None,
    deadline: float | Callable[[], float],
    on_output: OutputCallback | None = None,
    on_reset: ResetCallback | None = None,
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
        on_output: Optional async callback fired once per output as it
            arrives (live streaming). Stream chunks are passed one by one,
            although the returned list merges consecutive ones.
        on_reset: Optional async callback fired with the full output list
            when it changed other than by an append (clear_output,
            update_display_data). Callback errors are logged and disable
            both callbacks; they never abort execution.

    Returns:
        Tuple of (outputs, status, error_msg)
    """
    cell = _CellOutputs(on_output, on_reset)
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

        display_id = (content.get("transient") or {}).get("display_id")

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
            await cell.add(
                {
                    "output_type": "execute_result",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {}),
                    "execution_count": execution_count or result_exec_count,
                },
                display_id,
            )

        elif msg_type == "stream":
            text = content.get("text", "")
            await cell.add(
                {
                    "output_type": "stream",
                    "name": content.get("name", "stdout"),
                    "text": "".join(text) if isinstance(text, list) else text,
                },
                None,
            )

        elif msg_type == "display_data":
            await cell.add(
                {
                    "output_type": "display_data",
                    "data": content.get("data", {}),
                    "metadata": content.get("metadata", {}),
                },
                display_id,
            )

        elif msg_type == "update_display_data":
            if display_id is not None:
                await cell.update(
                    display_id, content.get("data", {}), content.get("metadata", {})
                )

        elif msg_type == "clear_output":
            await cell.clear(wait=bool(content.get("wait", False)))

        elif msg_type == "error":
            status = "error"
            error_msg = "\n".join(content.get("traceback", []))
            await cell.add(
                {
                    "output_type": "error",
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": content.get("traceback", []),
                },
                None,
            )

        elif msg_type == "status" and content.get("execution_state") == "idle":
            # Execution complete
            break

    return cell.outputs, status, error_msg


async def execute_code_on_kernel(
    client: Any,
    code: str,
    kernel_id: str,
    timeout: float,
    on_output: OutputCallback | None = None,
    interrupt: InterruptFn | None = None,
    on_reset: ResetCallback | None = None,
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
        on_reset: Optional async callback fired with the full output list
            after a clear_output or update_display_data; see collect_outputs
        interrupt: Optional best-effort kernel interrupt
        interrupt_grace: Seconds to wait for idle after an interrupt;
            defaults to ``EXECUTION_CONFIG.interrupt_grace_secs``
        cancel_cleanup: Receives the background cleanup task started when
            this execution is cancelled (only if ``interrupt`` is given)

    Returns:
        dict with execution_count, outputs, status, and optional error
    """
    # Execute the code. Nobody can answer an input() prompt, so stdin is
    # refused: the kernel raises StdinNotImplementedError at once instead of
    # blocking on input_request until the timeout (10 hours by default).
    logger.info(f"Executing code in kernel {kernel_id}")
    msg_id = client.execute(code, allow_stdin=False)

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
            client,
            msg_id,
            None,
            lambda: iopub_deadline,
            on_output=on_output,
            on_reset=on_reset,
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
        reply = await _await_shell_reply(shell_task, iopub_task, kernel_id)
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


async def _await_shell_reply(
    shell_task: asyncio.Task[dict[str, Any] | None],
    iopub_task: asyncio.Task[tuple[list[dict], str, str | None]],
    kernel_id: str,
) -> dict[str, Any] | None:
    """Return the execute_reply, without waiting long once the kernel is idle.

    Normally the shell reply arrives around the same time iopub reports idle.
    If iopub finishes first and reached idle (not its own deadline), the
    reply gets SHELL_REPLY_GRACE_SECS before we continue without it.
    """
    await asyncio.wait({shell_task, iopub_task}, return_when=asyncio.FIRST_COMPLETED)
    if not shell_task.done():
        _, _, iopub_error = iopub_task.result()
        if iopub_error != COLLECT_TIMEOUT_MSG:
            await asyncio.wait({shell_task}, timeout=SHELL_REPLY_GRACE_SECS)
            if not shell_task.done():
                logger.warning(
                    f"Kernel {kernel_id} went idle but sent no execute_reply "
                    f"within {SHELL_REPLY_GRACE_SECS}s; continuing without it"
                )
                shell_task.cancel()
                return None
    return await shell_task
