"""Serve an ASGI app from inside a Tornado request handler.

The in-server ``/mcp`` endpoint reuses FastMCP's Starlette app (the same one
uvicorn serves in standalone mode) instead of re-implementing the MCP
transport. Tornado has no ASGI host, so this module provides the two small
pieces needed to run that app in-process on Jupyter Server's event loop:

- ``run_asgi()`` translates one Tornado request into an ASGI ``http`` scope,
  feeds the request body to the app, and forwards the response back to the
  handler, flushing every chunk so SSE streams reach the client as they are
  produced.
- ``EmbeddedASGIApp`` runs the app's lifespan (which starts FastMCP's
  streamable-HTTP session manager) in a single long-lived background task, as
  anyio cancel scopes must be entered and exited in the same task.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Protocol

from tornado.httputil import HTTPServerRequest, split_host_and_port
from tornado.iostream import StreamClosedError

logger = logging.getLogger(__name__)

ASGIMessage = MutableMapping[str, Any]
ASGIApp = Callable[
    [
        MutableMapping[str, Any],
        Callable[[], Awaitable[ASGIMessage]],
        Callable[[ASGIMessage], Awaitable[None]],
    ],
    Awaitable[None],
]

# How long stop() waits for the lifespan (session manager shutdown) to finish
# before cancelling it.
EMBEDDED_APP_STOP_TIMEOUT_SECS = 5.0


class ResponseTarget(Protocol):
    """The subset of ``tornado.web.RequestHandler`` the bridge writes to."""

    request: HTTPServerRequest

    def set_status(self, status_code: int) -> None: ...
    def set_header(self, name: str, value: str) -> None: ...
    def add_header(self, name: str, value: str) -> None: ...
    def clear_header(self, name: str) -> None: ...
    def write(self, chunk: bytes) -> None: ...
    def flush(self) -> Awaitable[None]: ...


def _build_scope(
    request: HTTPServerRequest, path: str, root_path: str
) -> dict[str, Any]:
    """Build an ASGI ``http`` scope for ``request`` addressed to ``path``.

    Per the ASGI spec ``scope["path"]`` includes ``root_path``; Starlette strips
    the prefix again when routing, so the app sees ``path`` whatever the
    Jupyter base_url is (including a base_url equal to ``path`` itself).
    """
    full_path = f"{root_path}{path}"
    host, port = split_host_and_port(request.host)
    default_port = 443 if request.protocol == "https" else 80
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": request.version.removeprefix("HTTP/"),
        "method": request.method,
        "scheme": request.protocol,
        "path": full_path,
        "raw_path": full_path.encode("latin-1"),
        "root_path": root_path,
        "query_string": request.query.encode("latin-1"),
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in request.headers.get_all()
        ],
        "client": (request.remote_ip, 0) if request.remote_ip else None,
        "server": (host, port or default_port),
    }


async def run_asgi(
    app: ASGIApp,
    handler: ResponseTarget,
    *,
    path: str,
    root_path: str,
    disconnected: asyncio.Event,
) -> None:
    """Run ``app`` for the handler's current request and write its response.

    Args:
        app: The ASGI application to call.
        handler: The Tornado handler serving the request. The caller remains
            responsible for calling ``finish()`` afterwards.
        path: The path the app routes on (e.g. ``"/mcp"``), independent of the
            URL the client actually used.
        root_path: The URL prefix the app is mounted under (Jupyter base_url
            without the trailing slash).
        disconnected: Set by the handler when the client goes away. Once set,
            the app receives ``http.disconnect`` and further output is dropped.

    Raises:
        RuntimeError: If the app breaks the ASGI response protocol (sends a
            body before ``http.response.start``, or returns without starting
            a response while the client is still connected).
        Exception: Whatever the app raises; the caller turns it into an error
            response when headers have not been sent yet.
    """
    request = handler.request
    body_delivered = False
    response_started = False

    async def receive() -> ASGIMessage:
        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {
                "type": "http.request",
                "body": request.body or b"",
                "more_body": False,
            }
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def flush() -> None:
        try:
            await handler.flush()
        except StreamClosedError:
            logger.debug("MCP client closed the connection mid-response")
            disconnected.set()

    async def send(message: ASGIMessage) -> None:
        nonlocal response_started
        if disconnected.is_set():
            return

        if message["type"] == "http.response.start":
            response_started = True
            handler.set_status(message["status"])
            # Drop Tornado's default text/html type; the app sets its own.
            handler.clear_header("Content-Type")
            seen: set[str] = set()
            for raw_name, raw_value in message.get("headers", []):
                name = raw_name.decode("latin-1")
                value = raw_value.decode("latin-1")
                if name.lower() in seen:
                    handler.add_header(name, value)
                else:
                    handler.set_header(name, value)
                    seen.add(name.lower())
            # Send headers right away: a GET SSE stream may stay silent for a
            # long time, and clients wait for headers before reading events.
            await flush()
        elif message["type"] == "http.response.body":
            if not response_started:
                msg = "ASGI app sent a body before http.response.start"
                raise RuntimeError(msg)
            body = message.get("body", b"")
            if body:
                handler.write(body)
                await flush()

    scope = _build_scope(request, path=path, root_path=root_path)
    await app(scope, receive, send)

    if not response_started and not disconnected.is_set():
        msg = "ASGI app returned without starting a response"
        raise RuntimeError(msg)


class EmbeddedASGIApp:
    """Keep an ASGI app's lifespan running for in-process request handling."""

    def __init__(
        self,
        app: Any,
        *,
        stop_timeout: float = EMBEDDED_APP_STOP_TIMEOUT_SECS,
    ) -> None:
        """Wrap ``app``, a Starlette application with a lifespan."""
        self.app = app
        self._stop_timeout = stop_timeout
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        """Whether the lifespan has started and not yet stopped."""
        return self._task is not None and not self._task.done() and self._ready.is_set()

    async def _run(self) -> None:
        async with self.app.router.lifespan_context(self.app):
            self._ready.set()
            await self._stop.wait()

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Log a lifespan that ends while it should still be serving."""
        if self._stop.is_set() or not self._ready.is_set():
            return  # normal shutdown, or a startup failure start() reports
        if task.cancelled():
            logger.error("Embedded MCP app lifespan was cancelled; /mcp returns 503")
        elif task.exception() is not None:
            logger.error(
                "Embedded MCP app lifespan crashed; /mcp returns 503",
                exc_info=task.exception(),
            )

    async def start(self) -> None:
        """Start the lifespan and wait until the app is ready to serve.

        Raises:
            Exception: Whatever the lifespan raised during startup.
        """
        if self._task is not None:
            return

        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        task = asyncio.create_task(self._run())
        task.add_done_callback(self._on_task_done)
        self._task = task
        ready = asyncio.create_task(self._ready.wait())
        try:
            await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            # start() itself was cancelled: don't leave the lifespan running.
            ready.cancel()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._task = None
            raise

        if task.done():
            ready.cancel()
            self._task = None
            await task  # re-raises the startup failure
            msg = "ASGI app lifespan exited during startup"
            raise RuntimeError(msg)

    async def stop(self) -> None:
        """Shut the lifespan down; safe to call repeatedly or before start."""
        task = self._task
        if task is None:
            return

        self._stop.set()
        try:
            if task.done():
                # Already ended (crashed or cancelled); _on_task_done logged it.
                if not task.cancelled():
                    task.exception()  # mark the exception as retrieved
            else:
                await asyncio.wait_for(task, timeout=self._stop_timeout)
        except TimeoutError:
            logger.warning("Timed out stopping the embedded MCP app; cancelled it")
        except Exception:
            logger.warning("Embedded MCP app failed during shutdown", exc_info=True)
        finally:
            self._task = None
