"""Test the simplified MCP server functionality."""

import asyncio

import httpx
import pytest
from fastmcp import Client
from pydantic_monty import ResourceLimits

from jupyter_mcp_executor.mcp_server import (
    SANDBOX_EXECUTION_BUDGET_SECS,
    SANDBOX_MAX_MEMORY_BYTES,
    MCPServer,
    build_code_mode,
    build_sandbox_limits,
)


def simple_function(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y


async def async_function(name: str) -> str:
    """Async greeting function."""
    await asyncio.sleep(0.001)  # Small delay
    return f"Hello, {name}!"


def function_with_docstring(message: str) -> str:
    """Print a message to stdout.

    This is a more detailed description of what this function does.
    """
    print(message)
    return f"Printed: {message}"


class TestMCPServer:
    """Test MCPServer functionality."""

    def test_server_creation(self):
        """Test basic server creation."""
        server = MCPServer()

        assert server.name == "Jupyter MCP Server"
        assert server.port == 3001
        assert server.host == "localhost"
        assert server.mcp is not None
        assert len(server._registered_tools) == 0

    def test_server_creation_with_params(self):
        """Test server creation with custom parameters."""
        server = MCPServer(name="Test Server", port=3050, host="0.0.0.0")

        assert server.name == "Test Server"
        assert server.port == 3050
        assert server.host == "0.0.0.0"
        assert server.mcp is not None

    def test_register_single_tool(self):
        """Test registering a single tool."""
        server = MCPServer()

        server.register_tool(simple_function)

        # Check tool was registered
        assert len(server._registered_tools) == 1
        assert "simple_function" in server._registered_tools

        tool_info = server._registered_tools["simple_function"]
        assert tool_info["name"] == "simple_function"
        assert tool_info["description"] == "Add two numbers."
        assert tool_info["function"] == simple_function
        assert tool_info["is_async"] is False

    def test_register_tool_with_custom_name(self):
        """Test registering a tool with custom name."""
        server = MCPServer()

        server.register_tool(simple_function, name="add_numbers")

        assert "add_numbers" in server._registered_tools
        assert "simple_function" not in server._registered_tools

        tool_info = server._registered_tools["add_numbers"]
        assert tool_info["name"] == "add_numbers"

    def test_register_tool_with_custom_description(self):
        """Test registering a tool with custom description."""
        server = MCPServer()

        server.register_tool(simple_function, description="Custom description")

        tool_info = server._registered_tools["simple_function"]
        assert tool_info["description"] == "Custom description"

    def test_register_async_tool(self):
        """Test registering an async tool."""
        server = MCPServer()

        server.register_tool(async_function)

        tool_info = server._registered_tools["async_function"]
        assert tool_info["is_async"] is True

    def test_register_tools_as_list(self):
        """Test registering multiple tools as a list."""
        server = MCPServer()

        server.register_tools([simple_function, async_function])

        assert len(server._registered_tools) == 2
        assert "simple_function" in server._registered_tools
        assert "async_function" in server._registered_tools

    def test_register_tools_as_dict(self):
        """Test registering multiple tools as a dict."""
        server = MCPServer()

        tools = {"add": simple_function, "greet": async_function}
        server.register_tools(tools)

        assert len(server._registered_tools) == 2
        assert "add" in server._registered_tools
        assert "greet" in server._registered_tools

        assert server._registered_tools["add"]["function"] == simple_function
        assert server._registered_tools["greet"]["function"] == async_function

    def test_register_tools_invalid_type(self):
        """Test registering tools with invalid type."""
        server = MCPServer()

        with pytest.raises(
            TypeError, match="tools must be a list of functions or dict"
        ):
            server.register_tools("invalid")

    def test_list_tools(self):
        """Test listing registered tools."""
        server = MCPServer()

        # Initially empty
        assert server.list_tools() == []

        # After registering tools
        server.register_tool(simple_function)
        server.register_tool(function_with_docstring)

        tools = server.list_tools()
        assert len(tools) == 2

        tool_names = [t["name"] for t in tools]
        assert "simple_function" in tool_names
        assert "function_with_docstring" in tool_names

        # Check structure
        for tool in tools:
            assert "name" in tool
            assert "description" in tool

    def test_get_tool_info(self):
        """Test getting tool information."""
        server = MCPServer()

        # Non-existent tool
        assert server.get_tool_info("nonexistent") is None

        # Existing tool
        server.register_tool(simple_function)
        info = server.get_tool_info("simple_function")

        assert info is not None
        assert info["name"] == "simple_function"
        assert info["function"] == simple_function


class TestMCPServerDirect:
    """Test direct MCPServer instantiation."""

    def test_create_server_defaults(self):
        """Test creating server with defaults."""
        server = MCPServer()

        assert isinstance(server, MCPServer)
        assert server.name == "Jupyter MCP Server"
        assert server.port == 3001

    def test_create_server_with_params(self):
        """Test creating server with custom parameters."""
        server = MCPServer(name="Custom Server", port=3055)

        assert server.name == "Custom Server"
        assert server.port == 3055


class TestCodeModeSandboxLimits:
    """The Code Mode sandbox budget must not abort long kernel executions.

    Tool calls such as execute_cell can run for EXECUTION_CONFIG.default_timeout.
    Monty (fastmcp >= 4.1) pauses its clock while the script waits on the
    host, so only the script's own execution time is budgeted.
    """

    def test_budgets_sandbox_execution_only(self):
        assert (
            build_sandbox_limits()["max_feed_duration_secs"]
            == SANDBOX_EXECUTION_BUDGET_SECS
        )

    def test_keeps_a_memory_cap(self):
        """Passing limits replaces fastmcp's defaults, including 100 MB of memory.

        Monty treats an omitted key as "no limit", so a prompt-injected script
        could otherwise allocate without bound.
        """
        assert build_sandbox_limits()["max_memory"] == SANDBOX_MAX_MEMORY_BYTES

    def test_limits_match_installed_monty(self):
        """Every key we pass must be one the installed Monty accepts."""
        supported = ResourceLimits.__annotations__.keys()
        limits = build_code_mode().sandbox_provider.limits

        assert limits
        assert limits.keys() <= supported

    def test_server_uses_explicit_sandbox_limits(self):
        """MCPServer must never fall back to the fastmcp default budget."""
        server = MCPServer()

        transforms = [
            t
            for t in getattr(server.mcp, "transforms", [])
            if hasattr(t, "sandbox_provider")
        ]
        assert transforms, "CodeMode transform not found on FastMCP instance"
        assert transforms[0].sandbox_provider.limits == build_sandbox_limits()

    @pytest.mark.asyncio
    async def test_slow_tool_call_does_not_exhaust_budget(self, monkeypatch):
        """A tool call longer than the budget must not abort the script.

        This is the Monty property the budget relies on: time spent waiting
        on tool calls (kernel execution) does not count against it.
        """
        monkeypatch.setattr(
            "jupyter_mcp_executor.mcp_server.SANDBOX_EXECUTION_BUDGET_SECS", 0.5
        )

        async def slow_tool() -> str:
            """Take longer than the sandbox budget."""
            await asyncio.sleep(1.5)
            return "done"

        server = MCPServer(name="Budget")
        server.register_tool(slow_tool)
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": "return await call_tool('slow_tool', {})"}
            )

        assert "done" in result.content[0].text


class TestMCPServerIntegration:
    """Integration tests for MCP server."""

    def test_server_with_multiple_tools(self):
        """Test server with multiple different types of tools."""
        server = MCPServer(name="Integration Test Server")

        # Register various types of tools
        server.register_tool(simple_function)
        server.register_tool(async_function)
        server.register_tool(function_with_docstring, name="printer")

        # Verify all registered
        assert len(server._registered_tools) == 3
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]

        assert "simple_function" in tool_names
        assert "async_function" in tool_names
        assert "printer" in tool_names

        # Verify async detection
        assert server._registered_tools["simple_function"]["is_async"] is False
        assert server._registered_tools["async_function"]["is_async"] is True
        assert server._registered_tools["printer"]["is_async"] is False


class TestToolRegistration:
    """Tools are registered with FastMCP as-is, signature and all.

    Clients reach configured tools only through Code Mode, where a script
    passes ``call_tool(name, params)`` a Python dict: dict and list
    arguments arrive as JSON objects/arrays, never as JSON-encoded strings.
    """

    @staticmethod
    async def _input_schema(server: MCPServer, name: str) -> dict:
        tool = await server.mcp.get_tool(name)
        return tool.parameters

    @pytest.mark.asyncio
    async def test_dict_parameter_schema_is_a_plain_object(self):
        """A dict parameter is advertised as an object, not object-or-string."""

        def configure(options: dict[str, int], extra: dict | None = None) -> str:
            """Take dict parameters."""
            return "ok"

        server = MCPServer()
        server.register_tool(configure)

        properties = (await self._input_schema(server, "configure"))["properties"]
        assert properties["options"] == {
            "additionalProperties": {"type": "integer"},
            "type": "object",
        }
        assert properties["extra"]["anyOf"] == [
            {"additionalProperties": True, "type": "object"},
            {"type": "null"},
        ]

    @pytest.mark.asyncio
    async def test_code_mode_script_passes_dict_and_list_arguments(self):
        """A configured tool taking dict/list parameters works from a script."""

        def summarize(config: dict[str, int], tags: list[str]) -> str:
            """Describe the received argument types."""
            return (
                f"{type(config).__name__}:{sorted(config.items())} "
                f"{type(tags).__name__}:{tags}"
            )

        server = MCPServer()
        server.register_tool(summarize)
        code = (
            "return await call_tool('summarize', "
            "{'config': {'b': 2, 'a': 1}, 'tags': ['x', 'y']})"
        )
        async with Client(server.mcp) as client:
            result = await client.call_tool("execute", {"code": code})

        assert "dict:[('a', 1), ('b', 2)] list:['x', 'y']" in result.content[0].text

    @pytest.mark.asyncio
    async def test_json_string_for_dict_parameter_is_rejected(self):
        """A JSON-encoded string is a type error, not silently parsed or passed."""
        received = []

        def take_config(config: dict) -> str:
            """Record what arrived."""
            received.append(config)
            return "called"

        server = MCPServer()
        server.register_tool(take_config)
        code = "return await call_tool('take_config', {'config': '{\"a\": 1}'})"
        async with Client(server.mcp) as client:
            result = await client.call_tool(
                "execute", {"code": code}, raise_on_error=False
            )

        assert result.is_error
        assert "valid dictionary" in result.content[0].text
        assert received == []

    @pytest.mark.asyncio
    async def test_tool_keeps_name_docstring_and_signature(self):
        """Sync, async and unannotated callables register unchanged."""

        def untyped(value):
            """Echo the value."""
            return value

        server = MCPServer()
        server.register_tool(simple_function)
        server.register_tool(async_function)
        server.register_tool(untyped)

        add = await server.mcp.get_tool("simple_function")
        assert add.description == "Add two numbers."
        assert add.parameters["properties"] == {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        }
        assert (await server.mcp.get_tool("untyped")).parameters["properties"] == {
            "value": {}
        }
        code = (
            "a = await call_tool('simple_function', {'x': 2, 'y': 3})\n"
            "b = await call_tool('async_function', {'name': 'Ada'})\n"
            "c = await call_tool('untyped', {'value': [1, 'two']})\n"
            "return [a, b, c]"
        )
        async with Client(server.mcp) as client:
            result = await client.call_tool("execute", {"code": code})

        text = result.content[0].text
        assert "5" in text
        assert "Hello, Ada!" in text
        assert "two" in text

    @pytest.mark.asyncio
    async def test_register_tools_dict_uses_the_given_name(self):
        """register_tools({name: func}) exposes the tool under that name."""
        server = MCPServer()
        server.register_tools({"printer": function_with_docstring})

        assert await server.mcp.get_tool("printer") is not None
        assert await server.mcp.get_tool("function_with_docstring") is None


class TestEmbeddedApp:
    """The in-process app behind the authenticated Jupyter endpoint."""

    @pytest.mark.asyncio
    async def test_start_and_stop_embedded(self):
        server = MCPServer(name="Embedded Test")

        await server.start_embedded()
        embedded = server.embedded_app
        try:
            assert embedded is not None
            assert embedded.running
            assert embedded.app.state.path == "/mcp"

            # Starting again keeps the same running app.
            await server.start_embedded()
            assert server.embedded_app is embedded
        finally:
            await server.stop_embedded()

        assert server.embedded_app is None
        assert not embedded.running
        # Stopping again is a no-op.
        await server.stop_embedded()

    def test_build_http_app_returns_independent_apps(self):
        server = MCPServer(name="Embedded Test")

        assert server.build_http_app() is not server.build_http_app()


class TestStandaloneHostOriginGuard:
    """The unauthenticated standalone app rejects DNS-rebinding requests."""

    @staticmethod
    async def _post(app, host: str, origin: str | None = None) -> int:
        headers = {
            "Host": host,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if origin:
            headers["Origin"] = origin
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:3001"
        ) as client:
            response = await client.post("/mcp", headers=headers, content=b"{}")
        return response.status_code

    @pytest.mark.asyncio
    async def test_standalone_app_rejects_foreign_host(self):
        app = MCPServer(name="Guarded").build_standalone_app()

        assert await self._post(app, "evil.example:3001") == 421

    @pytest.mark.asyncio
    async def test_standalone_app_rejects_foreign_origin(self):
        app = MCPServer(name="Guarded").build_standalone_app()

        status = await self._post(app, "localhost:3001", origin="http://evil.example")

        assert status == 403

    @pytest.mark.asyncio
    async def test_embedded_app_leaves_host_checks_to_jupyter(self):
        """Jupyter's check_host guards the embedded app; no second guard."""
        server = MCPServer(name="Embedded")
        await server.start_embedded()
        try:
            status = await self._post(server.embedded_app.app, "hub.example.org")
        finally:
            await server.stop_embedded()

        # Reaches the MCP transport (which rejects the bad body), rather than
        # being stopped by a Host guard with 421.
        assert status != 421
