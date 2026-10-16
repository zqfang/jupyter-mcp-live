"""Jupyter Server extension for managing MCP server."""

import asyncio
import contextlib
import importlib
import importlib.metadata
import inspect
import logging
import os
import re

from jupyter_server.extension.application import ExtensionApp
from jupyter_server.utils import url_path_join
from traitlets import Bool, Int, List, TraitError, Unicode, validate

from .mcp_handler import MCPHandler
from .mcp_server import MCPServer

# Import notebook execution tools for context setup
try:
    from . import execution_tools

    NOTEBOOK_TOOLS_AVAILABLE = True
except ImportError:
    NOTEBOOK_TOOLS_AVAILABLE = False
    execution_tools = None

logger = logging.getLogger(__name__)


class MCPExtensionApp(ExtensionApp):
    """The Jupyter Server MCP extension app."""

    name = "jupyter_mcp_live"
    description = "Jupyter Server extension providing MCP server for tool registration"

    # Configurable traits
    mcp_jupyter_endpoint = Bool(
        default_value=True,
        help=(
            "Serve MCP on the Jupyter Server itself at "
            "{base_url}<mcp_endpoint_path>, behind Jupyter authentication "
            "(send 'Authorization: token <TOKEN>' or 'Bearer <TOKEN>'). Under "
            "JupyterHub this is reachable at /user/<name>/<mcp_endpoint_path> "
            "with a Hub API token."
        ),
    ).tag(config=True)

    mcp_endpoint_path = Unicode(
        default_value="mcp",
        help=(
            "Path of the authenticated MCP endpoint, relative to the Jupyter "
            "base_url. Change it if another extension already serves /mcp."
        ),
    ).tag(config=True)

    mcp_standalone = Bool(
        default_value=False,
        help=(
            "Also run the separate MCP listener on mcp_port. WARNING: that "
            "listener has NO authentication; anyone who can reach the port can "
            "run code in your kernels. Only enable it on a single-user machine."
        ),
    ).tag(config=True)

    mcp_port = Int(
        default_value=3001,
        help="Port for the standalone MCP listener (only used if mcp_standalone)",
    ).tag(config=True)

    mcp_name = Unicode(
        default_value="Jupyter MCP Server", help="Name for the MCP server"
    ).tag(config=True)

    mcp_tools = List(
        trait=Unicode(),
        default_value=[],
        help=(
            "List of tools to register with the MCP server. "
            "Format: 'module_path:function_name' "
            "(e.g., 'os:getcwd', 'math:sqrt')"
        ),
    ).tag(config=True)

    use_tool_discovery = Bool(
        default_value=True,
        help=(
            "Whether to automatically discover and register tools from "
            "Python entrypoints in the 'jupyter_server_mcp.tools' group"
        ),
    ).tag(config=True)

    mcp_server_instance: MCPServer | None = None
    mcp_server_task: asyncio.Task | None = None
    mcp_shutdown_timeout = 5

    def _load_function_from_string(self, tool_spec: str):
        """Load a function from a string specification.

        Args:
            tool_spec: Function specification in format
                'module_path:function_name'

        Returns:
            The loaded function object

        Raises:
            ValueError: If tool_spec format is invalid
            ImportError: If module cannot be imported
            AttributeError: If function not found in module
        """
        if ":" not in tool_spec:
            msg = (
                f"Invalid tool specification '{tool_spec}'. "
                f"Expected format: 'module_path:function_name'"
            )
            raise ValueError(msg)

        module_path, function_name = tool_spec.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
            return getattr(module, function_name)
        except ImportError as e:
            msg = f"Could not import module '{module_path}': {e}"
            raise ImportError(msg) from e
        except AttributeError as e:
            msg = f"Function '{function_name}' not found in module '{module_path}': {e}"
            raise AttributeError(msg) from e

    def _register_tools(self, tool_specs: list[str], source: str = "configuration"):
        """Register tools from a list of tool specifications.

        Args:
            tool_specs: List of tool specifications in 'module:function' format
            source: Description of where tools came from (for logging)
        """
        if not tool_specs:
            return

        logger.info(f"Registering {len(tool_specs)} tools from {source}")

        for tool_spec in tool_specs:
            try:
                function = self._load_function_from_string(tool_spec)
                self.mcp_server_instance.register_tool(function)
                logger.info(f"✅ Registered tool from {source}: {tool_spec}")
            except Exception as e:  # noqa: BLE001 - one bad tool must not block the rest
                logger.error(
                    f"❌ Failed to register tool '{tool_spec}' from {source}: {e}"
                )
                continue

    def _discover_entrypoint_tools(self) -> list[str]:
        """Discover tools from Python entrypoints in the 'jupyter_server_mcp.tools' group.

        Returns:
            List of tool specifications in 'module:function' format
        """
        if not self.use_tool_discovery:
            return []

        discovered_tools = []

        try:
            # Use importlib.metadata to discover entrypoints
            entrypoints = importlib.metadata.entry_points()
            # Handle both Python 3.10+ and 3.9 style entrypoint APIs
            if hasattr(entrypoints, "select"):
                tools_group = entrypoints.select(group="jupyter_mcp_live.tools")
            else:
                tools_group = entrypoints.get("jupyter_mcp_ex.tools", [])

            for entry_point in tools_group:
                try:
                    # Load the entrypoint value (can be a list or a function that returns a list)
                    loaded_value = entry_point.load()

                    # Get tool specs from either a list or callable
                    if isinstance(loaded_value, list):
                        tool_specs = loaded_value
                    elif callable(loaded_value):
                        tool_specs = loaded_value()
                        if not isinstance(tool_specs, list):
                            logger.warning(
                                f"Entrypoint '{entry_point.name}' function returned "
                                f"{type(tool_specs).__name__} instead of list, skipping"
                            )
                            continue
                    else:
                        logger.warning(
                            f"Entrypoint '{entry_point.name}' is neither a list nor callable, skipping"
                        )
                        continue

                    # Validate and collect tool specs
                    valid_specs = [spec for spec in tool_specs if isinstance(spec, str)]
                    invalid_count = len(tool_specs) - len(valid_specs)

                    if invalid_count > 0:
                        logger.warning(
                            f"Skipped {invalid_count} non-string tool specs from '{entry_point.name}'"
                        )

                    discovered_tools.extend(valid_specs)
                    logger.info(
                        f"Discovered {len(valid_specs)} tools from entrypoint '{entry_point.name}'"
                    )

                except Exception as e:  # noqa: BLE001 - skip a broken entrypoint
                    logger.error(f"Failed to load entrypoint '{entry_point.name}': {e}")
                    continue

        except Exception as e:  # noqa: BLE001 - discovery is optional
            logger.error(f"Failed to discover entrypoints: {e}")

        if not discovered_tools:
            logger.info("No tools discovered from entrypoints")

        return discovered_tools

    def initialize(self):
        """Initialize the extension."""
        super().initialize()
        # serverapp will be available as self.serverapp after parent initialization

    @validate("mcp_endpoint_path")
    def _validate_endpoint_path(self, proposal):
        path = proposal["value"].strip("/")
        if not path:
            msg = "mcp_endpoint_path must not be empty"
            raise TraitError(msg)
        return path

    def initialize_handlers(self):
        """Register the authenticated MCP endpoint (base_url is prepended)."""
        if not self.mcp_jupyter_endpoint:
            return
        pattern = rf"{re.escape(self.mcp_endpoint_path)}/?"
        self.handlers.append((pattern, MCPHandler, {"extension": self}))

    def _endpoint_url(self) -> str:
        """Return the URL clients use for the authenticated endpoint.

        Under JupyterHub the server's own connection_url is an internal
        address behind the Hub proxy, so prefer the public URL of this server
        (``JUPYTERHUB_PUBLIC_URL``), else its path on the proxy
        (``JUPYTERHUB_SERVICE_PREFIX``).
        """
        hub_public_url = os.environ.get("JUPYTERHUB_PUBLIC_URL")
        if hub_public_url:
            return url_path_join(hub_public_url, self.mcp_endpoint_path)
        hub_prefix = os.environ.get("JUPYTERHUB_SERVICE_PREFIX")
        if hub_prefix:
            return url_path_join(hub_prefix, self.mcp_endpoint_path)
        base = getattr(self.serverapp, "connection_url", "") or "/"
        return url_path_join(base, self.mcp_endpoint_path)

    def initialize_settings(self):
        """Initialize settings for the extension."""
        # Configuration is handled by traitlets

    async def _confirm_mcp_server_started(self):
        """Raise if the background MCP server task failed during startup."""
        task = self.mcp_server_task
        if task is None:
            return

        done, _ = await asyncio.wait(
            {task}, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            return

        await task
        msg = "MCP server exited during startup"
        raise RuntimeError(msg)

    async def _stop_mcp_server_task(self):
        """Stop the MCP server through its own shutdown path, then fall back."""
        if self.mcp_server_task is None or self.mcp_server_task.done():
            return

        self.log.info("Stopping MCP server")

        instance = self.mcp_server_instance
        stop_server = getattr(instance, "stop_server", None)
        if inspect.iscoroutinefunction(stop_server):
            await stop_server()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self.mcp_server_task),
                    timeout=self.mcp_shutdown_timeout,
                )
                return
            except TimeoutError:
                self.log.warning("Timed out waiting for MCP server to stop")

        self.mcp_server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.mcp_server_task

    async def _stop_embedded(self) -> None:
        """Stop the in-process app behind the authenticated endpoint."""
        stop_embedded = getattr(self.mcp_server_instance, "stop_embedded", None)
        if inspect.iscoroutinefunction(stop_embedded):
            await stop_embedded()

    def _bridge_package_logging(self) -> None:
        """Make package loggers use Jupyter Server's logging handlers/level."""
        # Parent logger for the whole package
        pkg_logger = logging.getLogger("jupyter_mcp_live")
        # Mirror server logger handlers and level
        pkg_logger.handlers.clear()
        for h in self.serverapp.log.handlers:
            pkg_logger.addHandler(h)
        pkg_logger.setLevel(self.serverapp.log.getEffectiveLevel())
        # Prevent double logging via root
        pkg_logger.propagate = False

    async def start_extension(self):
        """Start the extension - called after Jupyter Server starts."""
        try:
            self.log.info(f"Starting MCP server '{self.mcp_name}'")
            # Bridge package logging early so module-level loggers emit
            self._bridge_package_logging()

            # Set server context for notebook execution tools
            if NOTEBOOK_TOOLS_AVAILABLE:
                execution_tools.set_server_context(self.serverapp)
                self.log.info("Server context set for notebook execution tools")

            self.mcp_server_instance = MCPServer(
                parent=self, name=self.mcp_name, port=self.mcp_port
            )

            # Register tools from entrypoints, then from configuration
            entrypoint_tools = self._discover_entrypoint_tools()
            self._register_tools(entrypoint_tools, source="entrypoints")
            self._register_tools(self.mcp_tools, source="configuration")

            if self.mcp_jupyter_endpoint:
                await self.mcp_server_instance.start_embedded()
                self.log.info(
                    f"✅ MCP endpoint at {self._endpoint_url()} "
                    "(Jupyter/JupyterHub token required)"
                )

            if self.mcp_standalone:
                # Start the standalone listener in a background task
                self.mcp_server_task = asyncio.create_task(
                    self.mcp_server_instance.start_server()
                )
                await self._confirm_mcp_server_started()
                host = getattr(self.mcp_server_instance, "host", "localhost")
                self.log.warning(
                    f"MCP standalone listener started on {host}:{self.mcp_port} "
                    "WITHOUT authentication (Host/Origin checked only)"
                )

            if not (self.mcp_jupyter_endpoint or self.mcp_standalone):
                self.log.warning(
                    "MCP server has no endpoint: both mcp_jupyter_endpoint and "
                    "mcp_standalone are disabled"
                )

            registered_count = len(self.mcp_server_instance._registered_tools)
            self.log.info(f"Total registered tools: {registered_count}")

        except Exception as e:
            if self.mcp_server_task and not self.mcp_server_task.done():
                self.mcp_server_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.mcp_server_task
            try:
                await self._stop_embedded()
            except Exception:
                # Keep the original startup error; just record this one.
                self.log.exception("Failed to stop the MCP endpoint after error")
            finally:
                self.mcp_server_task = None
                self.mcp_server_instance = None
            self.log.error(f"Failed to start MCP server: {e}")
            raise

    async def _start_jupyter_server_extension(self, serverapp):  # noqa: ARG002
        """Start the extension - called after Jupyter Server starts."""
        await self.start_extension()

    async def stop_extension(self):
        """Stop the extension - called when Jupyter Server shuts down."""
        try:
            try:
                await self._stop_mcp_server_task()
            finally:
                await self._stop_embedded()
        finally:
            self.mcp_server_task = None
            self.mcp_server_instance = None
        self.log.info("MCP server stopped")
