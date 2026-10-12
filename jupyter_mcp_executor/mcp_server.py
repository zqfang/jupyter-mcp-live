"""Simple MCP server for registering Python functions as tools."""

import asyncio
import contextlib
import errno
import inspect
import json
import logging
import os
import socket
import sys
from collections.abc import Callable
from functools import wraps
from inspect import iscoroutinefunction, signature
from typing import Any, Union, get_args, get_origin

import uvicorn
from fastmcp import FastMCP
from fastmcp import settings as fastmcp_settings
from fastmcp.experimental.transforms.code_mode import (
    CodeMode,
    GetSchemas,
    ListTools,
    MontySandboxProvider,
)
from fastmcp.utilities.cli import log_server_banner
from pydantic_monty import ResourceLimits
from traitlets import Int, Unicode
from traitlets.config.configurable import LoggingConfigurable

from jupyter_mcp_executor.asgi_bridge import EmbeddedASGIApp
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

logger = logging.getLogger(__name__)

# Extra wall-clock budget for the Code Mode sandbox beyond a single kernel
# execution: the Monty time limit keeps ticking while the script awaits tool
# calls, so the budget must cover at least one full kernel execution
# (EXECUTION_CONFIG.default_timeout) plus orchestration overhead. Scripts that
# run several long cells should be split by the agent rather than covered here.
SANDBOX_TIMEOUT_HEADROOM_SECS = 300


def build_code_mode() -> CodeMode:
    """Build the Code Mode transform with an explicit sandbox time budget.

    Without an explicit ``max_duration_secs`` some fastmcp releases default
    the Monty script budget to 30s, which aborts any script whose tool calls
    include a long-running cell execution.
    """
    return CodeMode(
        discovery_tools=[ListTools(), GetSchemas()],
        sandbox_provider=MontySandboxProvider(
            limits=ResourceLimits(
                max_duration_secs=float(
                    EXECUTION_CONFIG.default_timeout + SANDBOX_TIMEOUT_HEADROOM_SECS
                )
            )
        ),
    )


class MCPServerPortError(RuntimeError):
    """Raised when the configured MCP server port cannot be bound."""


class _EmbeddedUvicornServer(uvicorn.Server):
    """Uvicorn server variant that leaves process signals to Jupyter Server."""

    @contextlib.contextmanager
    def capture_signals(self):
        """Do not install SIGINT/SIGTERM handlers for embedded servers."""
        yield


def _ensure_port_available(host: str, port: int) -> None:
    """Check whether Uvicorn will be able to bind to the configured address."""
    if port == 0:
        return

    try:
        addr_infos = socket.getaddrinfo(
            host or None,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
        reuse_address = os.name == "posix" and sys.platform != "cygwin"
        checked_any_address = False
        for family, socktype, proto, _canonname, sockaddr in set(addr_infos):
            try:
                sock = socket.socket(family, socktype, proto)
            except OSError:
                continue

            try:
                if reuse_address:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
                if family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6"):
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, True)
                sock.bind(sockaddr)
                checked_any_address = True
            except OSError as exc:
                if exc.errno == errno.EADDRNOTAVAIL:
                    continue
                raise
            finally:
                sock.close()

        if not checked_any_address:
            msg = f"could not bind on any address from {addr_infos}"
            raise OSError(msg)
    except OSError as exc:
        msg = (
            f"Cannot start MCP server on {host}:{port}: {exc.strerror or exc}. "
            "Configure another MCP port with "
            "c.MCPExtensionApp.mcp_port = <port> or "
            "--MCPExtensionApp.mcp_port=<port>."
        )
        raise MCPServerPortError(msg) from exc


def _is_dict_compatible_annotation(annotation) -> bool:
    """Check if an annotation expects dict values that can be JSON-converted."""
    # Direct dict annotation
    if annotation is dict:
        return True

    # Union types: Optional[dict], Union[dict, None], dict | None
    origin = get_origin(annotation)
    if origin is Union or (
        hasattr(annotation, "__class__")
        and annotation.__class__.__name__ == "UnionType"
    ):
        args = get_args(annotation)
        return dict in args

    # Typed dict annotations: Dict[K, V], dict[str, Any]
    return bool(hasattr(annotation, "__origin__") and annotation.__origin__ is dict)


def _wrap_with_json_conversion(func: Callable) -> Callable:
    """
    Wrapper that automatically converts JSON string arguments to dictionaries.

    This addresses the common issue where MCP clients pass dictionary arguments
    as JSON strings instead of structured objects. The wrapper inspects the
    function signature and attempts JSON parsing for parameters annotated as
    dict types when they are received as strings.

    Additionally, this function modifies the type annotations to accept Union[dict, str]
    for dict parameters to allow Pydantic validation to pass.

    This conversion is always applied to all registered tools to ensure compatibility
    with various MCP clients that may serialize dict parameters differently.

    Args:
        func: The function to wrap

    Returns:
        Wrapped function that handles JSON string conversion with modified annotations
    """
    sig = signature(func)

    def _should_convert_to_dict(annotation, value):
        """Check if a parameter should be converted from JSON string to dict."""
        return isinstance(value, str) and _is_dict_compatible_annotation(annotation)

    def _add_string_to_annotation(annotation):
        """Modify annotation to also accept strings for dict types."""
        # Direct dict annotation -> dict | str
        if annotation is dict:
            return dict | str

        # Union types: add str to existing union
        origin = get_origin(annotation)
        if origin is Union:
            args = get_args(annotation)
            if dict in args and str not in args:
                return Union[(*tuple(args), str)]
            return annotation

        # New Python 3.10+ union syntax: dict | None
        if (
            hasattr(annotation, "__class__")
            and annotation.__class__.__name__ == "UnionType"
        ):
            args = get_args(annotation)
            if dict in args and str not in args:
                # Reconstruct the union with str added
                new_args = (*tuple(args), str)
                # Create new union type
                result = new_args[0]
                for arg in new_args[1:]:
                    result = result | arg
                return result
            return annotation

        # Typed dict annotations -> annotation | str
        if hasattr(annotation, "__origin__") and annotation.__origin__ is dict:
            return annotation | str

        return annotation

    # Create new annotations that accept strings for dict parameters
    new_annotations = {}
    for param_name, param in sig.parameters.items():
        if param.annotation != inspect.Parameter.empty:
            new_annotations[param_name] = _add_string_to_annotation(param.annotation)
        else:
            new_annotations[param_name] = param.annotation

    # Keep the return annotation unchanged
    if hasattr(func, "__annotations__") and "return" in func.__annotations__:
        new_annotations["return"] = func.__annotations__["return"]

    if iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Convert keyword arguments that should be dicts but are strings
            converted_kwargs = {}
            for param_name, param_value in kwargs.items():
                if param_name in sig.parameters:
                    param = sig.parameters[param_name]
                    if _should_convert_to_dict(param.annotation, param_value):
                        try:
                            converted_kwargs[param_name] = json.loads(param_value)
                            logger.debug(
                                f"Converted JSON string to dict for parameter '{param_name}': {param_value}"
                            )
                        except json.JSONDecodeError:
                            # If it's not valid JSON, pass the string as-is
                            converted_kwargs[param_name] = param_value
                    else:
                        converted_kwargs[param_name] = param_value
                else:
                    converted_kwargs[param_name] = param_value

            return await func(*args, **converted_kwargs)

        # Set the modified annotations on the wrapper
        async_wrapper.__annotations__ = new_annotations
        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Convert keyword arguments that should be dicts but are strings
        converted_kwargs = {}
        for param_name, param_value in kwargs.items():
            if param_name in sig.parameters:
                param = sig.parameters[param_name]
                if _should_convert_to_dict(param.annotation, param_value):
                    try:
                        converted_kwargs[param_name] = json.loads(param_value)
                        logger.debug(
                            f"Converted JSON string to dict for parameter '{param_name}': {param_value}"
                        )
                    except json.JSONDecodeError:
                        # If it's not valid JSON, pass the string as-is
                        converted_kwargs[param_name] = param_value
                else:
                    converted_kwargs[param_name] = param_value
            else:
                converted_kwargs[param_name] = param_value

        return func(*args, **converted_kwargs)

    # Set the modified annotations on the wrapper
    sync_wrapper.__annotations__ = new_annotations
    return sync_wrapper


class MCPServer(LoggingConfigurable):
    """Simple MCP server that allows registering Python functions as tools."""

    # Configurable traits
    name = Unicode(
        default_value="Jupyter MCP Server", help="Name for the MCP server"
    ).tag(config=True)

    port = Int(default_value=3001, help="Port for the MCP server to listen on").tag(
        config=True
    )

    host = Unicode(
        default_value="localhost", help="Host for the MCP server to listen on"
    ).tag(config=True)

    def __init__(self, **kwargs):
        """Initialize the MCP server.

        Args:
            **kwargs: Configuration parameters
        """
        super().__init__(**kwargs)
        ## TODO: when tools become more dynamic or we have a large number of tools, we can consider using a discovery tool like Search or GetTags instead of ListTools
        ## 3 stage
        # code_mode = CodeMode(discovery_tools=[GetTags(), Search(), GetSchemas()])
        # 2 stage, best for smaller catalogs
        ## see https://gofastmcp.com/servers/transforms/code-mode
        ## code_mode = CodeMode(discovery_tools=[Search(default_detail="detailed"), GetSchemas()])
        # for under 20 tools, ListTools let LLM see everything upfront can be faster than multiple search round-trips
        code_mode = build_code_mode()
        # Initialize FastMCP and tools registry
        self.mcp = FastMCP(self.name, transforms=[code_mode])
        self._registered_tools = {}
        # Set by _run_http_async_without_signals; stop_server() must not
        # AttributeError when called before the server task has started.
        self._uvicorn_server: uvicorn.Server | None = None
        # Set by start_embedded(): the FastMCP app served in-process through
        # the authenticated Jupyter Server handler (see mcp_handler.py).
        self.embedded_app: EmbeddedASGIApp | None = None
        self.log.info(
            f"Initialized MCP server '{self.name}' on {self.host}:{self.port}"
        )

    def register_tool(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
    ):
        """Register a Python function as an MCP tool.

        Args:
            func: Python function to register
            name: Optional tool name (defaults to function name)
            description: Optional tool description (defaults to function
                docstring)
        """
        tool_name = name or func.__name__
        tool_description = description or func.__doc__ or f"Tool: {tool_name}"

        self.log.info(f"Registering tool: {tool_name}")
        self.log.debug(
            f"Tool details - Name: {tool_name}, "
            f"Description: {tool_description}, Async: {iscoroutinefunction(func)}"
        )

        # Apply auto-conversion wrapper (always enabled)
        registered_func = _wrap_with_json_conversion(func)
        self.log.debug(f"Applied JSON argument auto-conversion wrapper to {tool_name}")

        self.mcp.tool(registered_func)

        # Keep track for listing
        self._registered_tools[tool_name] = {
            "name": tool_name,
            "description": tool_description,
            "function": func,
            "is_async": iscoroutinefunction(func),
        }

    def register_tools(self, tools: list[Callable] | dict[str, Callable]):
        """Register multiple Python functions as MCP tools.

        Args:
            tools: List of functions or dict mapping names to functions
        """
        if isinstance(tools, list):
            for func in tools:
                self.register_tool(func)
        elif isinstance(tools, dict):
            for name, func in tools.items():
                self.register_tool(func, name=name)
        else:
            msg = "tools must be a list of functions or dict mapping names to functions"
            raise TypeError(msg)

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools."""
        return [
            {"name": tool["name"], "description": tool["description"]}
            for tool in self._registered_tools.values()
        ]

    def get_tool_info(self, tool_name: str) -> dict[str, Any] | None:
        """Get information about a specific tool."""
        return self._registered_tools.get(tool_name)

    def build_http_app(self) -> Any:
        """Build FastMCP's streamable-HTTP Starlette app for the Jupyter handler.

        Each call returns a new app with its own session manager; FastMCP's
        server lifespan is reference-counted, so the standalone (uvicorn) and
        embedded (Jupyter handler) apps can run side by side. No Host/Origin
        guard here: Jupyter's check_host and authentication run first, and
        under JupyterHub the Host is the Hub's public name. Disabled explicitly
        because some fastmcp releases (e.g. 3.4.3) enable the guard by default.
        """
        return self.mcp.http_app(transport="http", host_origin_protection=False)

    def build_standalone_app(self) -> Any:
        """Build the app for the standalone listener, with a Host/Origin guard.

        The standalone listener has no authentication, so at least reject
        DNS-rebinding and cross-origin browser requests: Host must be a
        loopback name or the bound address (421 otherwise), and a browser
        Origin must match (403 otherwise).
        """
        return self.mcp.http_app(transport="http", host_origin_protection=True)

    async def start_embedded(self) -> None:
        """Start the app served in-process by the Jupyter Server handler."""
        if self.embedded_app is not None:
            return
        embedded = EmbeddedASGIApp(self.build_http_app())
        await embedded.start()
        self.embedded_app = embedded
        self.log.info(f"Registered tools: {list(self._registered_tools.keys())}")

    async def stop_embedded(self) -> None:
        """Stop the in-process app; safe to call when it is not running."""
        embedded = self.embedded_app
        self.embedded_app = None
        if embedded is not None:
            await embedded.stop()

    async def _run_http_async_without_signals(self, host: str, port: int) -> None:
        """Run FastMCP over HTTP without taking over process signal handlers."""
        transport = "http"
        app = self.build_standalone_app()

        log_server_banner(server=self.mcp)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            timeout_graceful_shutdown=2,
            lifespan="on",
            ws="websockets-sansio",
            log_level=fastmcp_settings.log_level.lower(),
        )
        server = _EmbeddedUvicornServer(config)
        self._uvicorn_server = server
        path = getattr(app.state, "path", "").lstrip("/")
        self.log.info(
            f"Starting MCP server {self.name!r} with transport "
            f"{transport!r} on http://{host}:{port}/{path}"
        )

        try:
            await server.serve()
        except asyncio.CancelledError:
            server.should_exit = True
            if getattr(server, "started", False):
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await server.shutdown()
            raise
        finally:
            if self._uvicorn_server is server:
                self._uvicorn_server = None

    async def stop_server(self) -> None:
        """Request a graceful MCP HTTP server shutdown."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

    async def start_server(self, host: str | None = None):
        """Start the MCP server on the specified host and port."""
        server_host = host or self.host
        _ensure_port_available(server_host, self.port)

        self.log.info(f"Registered tools: {list(self._registered_tools.keys())}")

        await self._run_http_async_without_signals(host=server_host, port=self.port)
