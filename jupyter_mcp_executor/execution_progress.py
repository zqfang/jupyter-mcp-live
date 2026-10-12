"""MCP progress notifications while a cell executes.

Cell executions can run for a long time, and the MCP client otherwise hears
nothing until the result arrives. ``ExecutionProgress`` sends
``notifications/progress`` for the current MCP request:

- when outputs arrive (throttled to ``min_interval``), with a one-line summary
  of the latest output, and
- as a heartbeat every ``heartbeat_interval`` while nothing else was sent, so
  silent cells still report.

Progress comes from FastMCP's request context, so it also reaches the client
when the tool runs inside a Code Mode ``execute`` script, and the tools need no
extra parameter. Without a request context (direct calls, tests) there is no
reporter. A failing notification never affects the execution itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Self

from fastmcp.server.dependencies import get_context

from jupyter_mcp_executor.notebook_config import PROGRESS_CONFIG, ProgressConfig

logger = logging.getLogger(__name__)

# (progress, total, message) -> sends one notifications/progress
ReportFn = Callable[[float, float | None, str], Awaitable[None]]

# Minimum step that keeps progress strictly increasing, as MCP requires.
_PROGRESS_EPSILON = 0.001


def _summarize(output: dict[str, Any]) -> str:
    """Return a one-line summary of a kernel output for a progress message."""
    output_type = output.get("output_type")
    if output_type == "stream":
        lines = [line for line in str(output.get("text", "")).splitlines() if line]
        return lines[-1].strip() if lines else ""
    if output_type == "error":
        return f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
    if output_type == "execute_result":
        return "[result]"
    data = output.get("data", {})
    if any(str(mime).startswith("image/") for mime in data):
        return "[image]"
    return "[display]"


class ExecutionProgress:
    """Sends throttled progress notifications for one execution."""

    def __init__(
        self,
        report: ReportFn,
        label: str,
        *,
        config: ProgressConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a reporter.

        Args:
            report: Sends one notification (progress, total, message).
            label: Identifies the execution, e.g. ``"analysis.ipynb cell 3"``.
            config: Intervals and message length limit; defaults to the
                module's ``PROGRESS_CONFIG`` (read at call time).
            clock: Monotonic clock in seconds (injectable for tests).
        """
        self._report = report
        self._label = label
        self._config = config if config is not None else PROGRESS_CONFIG
        self._clock = clock
        self._start = clock()
        self._last_sent = self._start
        self._last_progress = 0.0
        self._outputs = 0
        self._last_summary = ""
        self._waiting = True
        self._enabled = True
        self._heartbeat: asyncio.Task[None] | None = None

    @classmethod
    def from_request(
        cls, label: str, *, config: ProgressConfig | None = None
    ) -> ExecutionProgress | None:
        """Bind a reporter to the current MCP request, if there is one."""
        try:
            ctx = get_context()
        except RuntimeError:
            return None

        async def report(progress: float, total: float | None, message: str) -> None:
            await ctx.report_progress(progress=progress, total=total, message=message)

        return cls(report, label, config=config)

    def mark_running(self) -> None:
        """The kernel is executing now (no longer waiting for the kernel lock)."""
        self._waiting = False

    async def on_output(self, output: dict[str, Any]) -> None:
        """Record an output; notify if ``min_interval`` passed since the last."""
        self._outputs += 1
        summary = _summarize(output)
        if summary:
            self._last_summary = summary
        if self._clock() - self._last_sent >= self._config.min_interval:
            await self.send()

    def _message(self, elapsed: float) -> str:
        parts = [self._label, f"{elapsed:.0f}s"]
        if self._waiting:
            parts.append("waiting for kernel")
        else:
            noun = "output" if self._outputs == 1 else "outputs"
            parts.append(f"{self._outputs} {noun}")
            if self._last_summary:
                parts.append(self._last_summary)
        message = " · ".join(parts)
        limit = self._config.message_max_chars
        if len(message) > limit:
            message = message[: limit - 1] + "…"
        return message

    async def send(self) -> None:
        """Send one notification now (no-op once disabled)."""
        if not self._enabled:
            return
        now = self._clock()
        elapsed = now - self._start
        progress = max(elapsed, self._last_progress + _PROGRESS_EPSILON)
        self._last_sent = now
        self._last_progress = progress
        try:
            await self._report(progress, None, self._message(elapsed))
        except Exception:  # progress must never break execution
            logger.debug("Progress notification failed; disabling", exc_info=True)
            self._enabled = False

    async def _beat(self) -> None:
        interval = self._config.heartbeat_interval
        while self._enabled:
            due = self._last_sent + interval
            await asyncio.sleep(max(due - self._clock(), 0.0))
            if self._clock() - self._last_sent >= interval:
                await self.send()

    async def __aenter__(self) -> Self:
        """Start the heartbeat."""
        self._heartbeat = asyncio.create_task(self._beat())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Stop the heartbeat, also when the execution raised."""
        task, self._heartbeat = self._heartbeat, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
