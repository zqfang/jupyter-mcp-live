"""Authenticated MCP endpoint served by Jupyter Server itself.

``MCPHandler`` exposes the FastMCP streamable-HTTP app at
``{base_url}<mcp_endpoint_path>`` (``/mcp`` by default) on the Jupyter port.
Requests are authenticated by Jupyter's IdentityProvider before they reach
FastMCP, so the endpoint accepts exactly the credentials Jupyter does:

- a local server's token (``Authorization: token <TOKEN>`` or ``Bearer``);
- under JupyterHub, a Hub API token with access to this user's server. The
  Hub proxy already routes ``/user/<name>/...`` here, so no extra port or
  proxy configuration is needed.

FastMCP still implements the whole MCP transport (sessions, SSE streams,
protocol negotiation, Code Mode); this handler only authenticates and
forwards through the in-process ASGI bridge.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from jupyter_server.auth.decorator import authorized
from jupyter_server.base.handlers import JupyterHandler
from tornado.web import HTTPError

from jupyter_mcp_live.asgi_bridge import run_asgi

if TYPE_CHECKING:
    from jupyter_mcp_live.extension import MCPExtensionApp

# MCP tools run code in kernels, so require the same permission as the
# kernels API rather than mere read access.
MCP_AUTH_ACTION = "execute"
MCP_AUTH_RESOURCE = "kernels"
MCP_AUTH_MESSAGE = "Not authorized to execute code in kernels"

# They also read and modify notebooks: a user the authorizer keeps from doing
# that through the contents API (e.g. a read-only collaborator) must not do
# it through MCP. Checked for the whole endpoint, not per tool.
MCP_CONTENTS_PERMISSIONS = (
    ("read", "Not authorized to read notebooks"),
    ("write", "Not authorized to modify notebooks"),
)


def _mcp_authorized(method: Callable[..., Any]) -> Callable[..., Any]:
    """Every permission the MCP tools need, as jupyter_server's @authorized.

    Each action is explicit: the decorator's default derives it from the
    HTTP method (GET -> "read"), which would let a read-only user open the
    SSE stream.
    """
    for action, message in MCP_CONTENTS_PERMISSIONS:
        method = authorized(action, "contents", message)(method)
    return authorized(MCP_AUTH_ACTION, MCP_AUTH_RESOURCE, MCP_AUTH_MESSAGE)(method)


class MCPHandler(JupyterHandler):
    """Forward authenticated requests to the in-process FastMCP app."""

    auth_resource = MCP_AUTH_RESOURCE

    def initialize(self, extension: MCPExtensionApp) -> None:
        """Receive the extension, whose MCP server is created after routing."""
        self._extension = extension
        self._disconnected = asyncio.Event()

    async def prepare(self) -> None:  # type: ignore[override]
        """Authenticate without redirecting; each method is @authorized."""
        # MCP clients are not browsers: answer 403 (from @authorized) instead
        # of redirecting to the login page. The parent also enforces Host
        # checks and XSRF for cookie-authenticated (browser) requests.
        await super().prepare(_redirect_to_login=False)

    def set_default_headers(self) -> None:
        """Keep proxies from caching or buffering MCP (SSE) responses."""
        super().set_default_headers()
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")

    def on_connection_close(self) -> None:
        """Let the MCP app see the client disconnect."""
        self._disconnected.set()

    @_mcp_authorized
    async def get(self) -> None:
        """Server-to-client SSE stream of a streamable-HTTP session."""
        await self._forward()

    @_mcp_authorized
    async def post(self) -> None:
        """Client JSON-RPC messages."""
        await self._forward()

    @_mcp_authorized
    async def delete(self) -> None:
        """Session termination."""
        await self._forward()

    async def _forward(self) -> None:
        server: Any = self._extension.mcp_server_instance
        embedded = getattr(server, "embedded_app", None)
        if embedded is None or not embedded.running:
            raise HTTPError(503, "MCP server is not running")

        try:
            await run_asgi(
                embedded.app,
                self,
                path=embedded.app.state.path,
                root_path=self.base_url.rstrip("/"),
                disconnected=self._disconnected,
            )
        except Exception as e:
            self.log.exception("MCP request failed")
            if not self._headers_written:
                raise HTTPError(500, "MCP request failed") from e
            # Headers (and maybe part of an SSE stream) are already out. Drop
            # the connection so the client sees a truncated response instead
            # of a cleanly terminated one it would mistake for success.
            self._disconnected.set()
            self.request.connection.close()
        # Tornado finishes the response once this coroutine returns.
