"""Forward images from tools called inside Code Mode scripts to the model.

Code Mode passes each nested ``call_tool()`` result into the sandbox as its
structured content, else as text (any other block is ``str()``-ed). Image
blocks would therefore never reach the model, or would flood the script with
base64. This middleware moves them out of nested results and appends them to
the ``execute`` response, which the client renders as images.

Nested calls go through the server's middleware chain because Code Mode
invokes them with ``FastMCP.call_tool`` (middleware applied by default); a
context variable tells them apart from top-level calls.
"""

from contextvars import ContextVar

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import ImageContent, TextContent

from jupyter_mcp_executor.notebook_config import IMAGE_CONFIG

# Images collected from nested calls of the execute request in progress;
# None outside an execute request. The list is shared (not copied) with the
# tasks the sandbox runs nested calls in, which inherit this context.
_collected_images: ContextVar[list[ImageContent] | None] = ContextVar(
    "code_mode_collected_images", default=None
)


class CodeModeImageForwarder(Middleware):
    """Attach images returned by nested tool calls to the execute response."""

    def __init__(
        self,
        execute_tool_name: str = "execute",
        max_images: int = IMAGE_CONFIG.max_images_per_response,
    ) -> None:
        self._execute_tool_name = execute_tool_name
        self._max_images = max_images

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        collected = _collected_images.get()
        if collected is not None:
            return _move_images(await call_next(context), collected)
        if context.message.name != self._execute_tool_name:
            return await call_next(context)

        sink: list[ImageContent] = []
        token = _collected_images.set(sink)
        try:
            result = await call_next(context)
        finally:
            _collected_images.reset(token)
        return _attach_images(result, sink, self._max_images)


def _move_images(result: ToolResult, sink: list[ImageContent]) -> ToolResult:
    """Return the nested result without images, which are added to ``sink``."""
    images = [c for c in result.content if isinstance(c, ImageContent)]
    if not images:
        return result
    sink.extend(images)
    note = TextContent(
        type="text",
        text=f"[{len(images)} image(s) attached to the execute response]",
    )
    rest = [c for c in result.content if not isinstance(c, ImageContent)]
    return result.model_copy(update={"content": [*rest, note]})


def _attach_images(
    result: ToolResult, images: list[ImageContent], max_images: int
) -> ToolResult:
    """Return the execute result with the collected images appended."""
    if not images:
        return result
    extra: list[TextContent] = []
    dropped = len(images) - max_images
    if dropped > 0:
        extra.append(
            TextContent(
                type="text",
                text=(
                    f"[{dropped} more image(s) not attached: at most {max_images} "
                    "per execute call; view them in a separate call]"
                ),
            )
        )
    return result.model_copy(
        update={"content": [*result.content, *images[:max_images], *extra]}
    )
