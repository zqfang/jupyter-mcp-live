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
import inspect
from typing import TYPE_CHECKING, Any

from jupyter_server.base.handlers import JupyterHandler
from tornado.web import HTTPError

from jupyter_mcp_executor.asgi_bridge import run_asgi

if TYPE_CHECKING:
    from jupyter_mcp_executor.extension import MCPExtensionApp

# MCP tools run code in kernels, so require the same permission as the
# kernels API rather than mere read access.
MCP_AUTH_ACTION = "execute"
MCP_AUTH_RESOURCE = "kernels"


class MCPHandler(JupyterHandler):
    """Forward authenticated requests to the in-process FastMCP app."""

    auth_resource = MCP_AUTH_RESOURCE

    def initialize(self, extension: MCPExtensionApp) -> None:
        """Receive the extension, whose MCP server is created after routing."""
        self._extension = extension
        self._disconnected = asyncio.Event()

    async def prepare(self) -> None:  # type: ignore[override]
        """Require an authenticated, authorized user for every request."""
        # MCP clients are not browsers: answer 403 instead of redirecting to
        # the login page. The parent also enforces Host checks and XSRF for
        # cookie-authenticated (browser) requests.
        await super().prepare(_redirect_to_login=False)
        user = self.current_user
        if not user:
            raise HTTPError(403, "Jupyter authentication required")

        authorized = self.authorizer.is_authorized(
            self, user, MCP_AUTH_ACTION, MCP_AUTH_RESOURCE
        )
        if inspect.isawaitable(authorized):
            authorized = await authorized
        if not authorized:
            raise HTTPError(403, "Not authorized to execute code in kernels")

    def set_default_headers(self) -> None:
        """Keep proxies from caching or buffering MCP (SSE) responses."""
        super().set_default_headers()
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")

    def on_connection_close(self) -> None:
        """Let the MCP app see the client disconnect."""
        self._disconnected.set()

    async def get(self) -> None:
        """Server-to-client SSE stream of a streamable-HTTP session."""
        await self._forward()

    async def post(self) -> None:
        """Client JSON-RPC messages."""
        await self._forward()

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
