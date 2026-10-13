"""End-to-end tests for the authenticated in-server MCP endpoint.

These start a real Jupyter Server (via pytest-jupyter) with the extension
enabled and talk to ``{base_url}mcp`` over HTTP, including with a real
FastMCP client, so routing, Jupyter auth, the ASGI bridge and FastMCP's
streamable-HTTP transport are all exercised together.
"""

import asyncio
import dataclasses
import json
import time

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from tornado.simple_httpclient import HTTPStreamClosedError

from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG, ProgressConfig

EXTENSION = "jupyter_mcp_executor"
TOY_TOOL = "tests.integration.toy_tools:add"
EXECUTION_TOOLS = [
    f"jupyter_mcp_executor.execution_tools:{name}"
    for name in (
        "setup_notebook",
        "add_code_cell",
        "shutdown_notebook",
        "interrupt_notebook_kernel",
        "restart_notebook_kernel",
    )
]
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def _mcp_headers(**extra: str) -> dict[str, str]:
    """Fresh headers per request: jp_fetch adds its auth header in place."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **extra,
    }


def _config(**extension_traits) -> dict:
    return {
        "ServerApp": {"jpserver_extensions": {EXTENSION: True}},
        "MCPExtensionApp": {
            "mcp_tools": [TOY_TOOL],
            "use_tool_discovery": False,
            **extension_traits,
        },
    }


def _token(serverapp) -> str:
    return serverapp.identity_provider.token


def _mcp_url(port: int, base_url: str, path: str = "mcp") -> str:
    return f"http://localhost:{port}{base_url}{path}"


@pytest.fixture
def mcp_extension(jp_serverapp, jp_asyncio_loop):
    """Start the extension (normally done by start_ioloop) and stop it after.

    Stopping matters: it shuts the embedded FastMCP lifespan down, so session
    managers and anyio streams do not leak into later tests.
    """
    jp_asyncio_loop.run_until_complete(
        jp_serverapp.extension_manager.start_all_extensions()
    )
    extension = jp_serverapp.extension_manager.extension_points[EXTENSION].app
    yield extension
    jp_asyncio_loop.run_until_complete(extension.stop_extension())


async def _initialize(jp_fetch, path: str = "mcp", **headers: str):
    return await jp_fetch(
        path,
        method="POST",
        body=json.dumps(INITIALIZE),
        headers=_mcp_headers(**headers),
        raise_error=False,
    )


class TestDefaultEndpoint:
    """Default configuration: /mcp on the Jupyter port, no standalone port."""

    @pytest.fixture
    def jp_server_config(self):
        return _config()

    @pytest.mark.usefixtures("mcp_extension")
    async def test_rejects_request_without_valid_token(self, jp_serverapp, jp_fetch):
        async def status(authorization: str) -> int:
            response = await _initialize(jp_fetch, Authorization=authorization)
            return response.code

        # The same request succeeds with the real token, so the 403 comes from
        # the endpoint's auth, not from a missing route (Jupyter's 404 page
        # also requires auth).
        assert await status(f"token {_token(jp_serverapp)}") == 200
        assert await status("token not-the-token") == 403
        assert await status("") == 403

    @pytest.mark.usefixtures("mcp_extension")
    async def test_initialize_with_token(self, jp_fetch):
        response = await _initialize(jp_fetch)

        assert response.code == 200
        assert response.headers.get("mcp-session-id")
        assert '"serverInfo"' in response.body.decode()

    async def test_no_standalone_listener_by_default(self, mcp_extension):
        assert mcp_extension.mcp_standalone is False
        assert mcp_extension.mcp_server_task is None
        assert mcp_extension.mcp_server_instance.embedded_app is not None

    # http_server makes the test server actually listen on jp_http_port.
    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_fastmcp_client_end_to_end_with_bearer_token(
        self, jp_serverapp, jp_http_port, jp_base_url
    ):
        transport = StreamableHttpTransport(
            _mcp_url(jp_http_port, jp_base_url),
            headers={"Authorization": f"Bearer {_token(jp_serverapp)}"},
        )

        # Exercises POST (initialize, calls), the GET SSE stream the client
        # opens after initialize, and DELETE when the session closes.
        async with Client(transport) as client:
            names = {tool.name for tool in await client.list_tools()}
            result = await client.call_tool(
                "execute",
                {"code": "return await call_tool('add', {'a': 2, 'b': 3})"},
            )

        assert {"list_tools", "get_schema", "execute"} <= names
        assert json.loads(result.content[0].text) == {"result": 5}

    async def test_returns_503_when_mcp_app_not_running(self, mcp_extension, jp_fetch):
        await mcp_extension.mcp_server_instance.stop_embedded()

        response = await _initialize(jp_fetch)

        assert response.code == 503

    @pytest.mark.usefixtures("mcp_extension")
    async def test_stream_error_after_headers_drops_connection(
        self, jp_fetch, monkeypatch
    ):
        """A crash mid-stream must not look like a cleanly finished response."""

        async def crash_mid_stream(_app, handler, **_kwargs):
            handler.set_status(200)
            handler.set_header("Content-Type", "text/event-stream")
            handler.write(b"data: partial\n\n")
            await handler.flush()
            msg = "app crashed mid-stream"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            "jupyter_mcp_executor.mcp_handler.run_asgi", crash_mid_stream
        )

        # The connection is dropped instead of ending the chunked body
        # normally, so the client sees a transport error, not a 200.
        with pytest.raises(HTTPStreamClosedError):
            await _initialize(jp_fetch)


class TestAuthorizerDenial:
    """An authenticated user without permission to execute code is refused."""

    @pytest.fixture
    def jp_server_config(self):
        config = _config()
        config["ServerApp"]["authorizer_class"] = (
            "tests.integration.toy_tools.DenyKernelExecuteAuthorizer"
        )
        return config

    @pytest.mark.usefixtures("mcp_extension")
    async def test_valid_token_without_kernel_execute_is_forbidden(self, jp_fetch):
        response = await _initialize(jp_fetch)

        assert response.code == 403


class TestCustomEndpointPath:
    """mcp_endpoint_path moves the endpoint (e.g. to avoid other /mcp users)."""

    @pytest.fixture
    def jp_server_config(self):
        return _config(mcp_endpoint_path="agent-mcp")

    @pytest.mark.usefixtures("mcp_extension")
    async def test_endpoint_served_at_custom_path(self, jp_fetch):
        moved = await _initialize(jp_fetch, path="agent-mcp")
        old = await _initialize(jp_fetch, path="mcp")

        assert moved.code == 200
        assert old.code == 404


class TestEndpointDisabled:
    """mcp_jupyter_endpoint=False registers no handler."""

    @pytest.fixture
    def jp_server_config(self):
        return _config(mcp_jupyter_endpoint=False)

    async def test_endpoint_not_registered(self, mcp_extension, jp_fetch):
        response = await _initialize(jp_fetch)

        assert response.code == 404
        assert mcp_extension.mcp_server_instance.embedded_app is None


class TestProgressNotifications:
    """A real cell reports MCP progress while it runs, through Code Mode."""

    @pytest.fixture
    def jp_server_config(self):
        return _config(mcp_tools=[TOY_TOOL, *EXECUTION_TOOLS])

    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_progress_arrives_while_cell_runs(
        self, jp_serverapp, jp_http_port, jp_base_url, monkeypatch
    ):
        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_progress.PROGRESS_CONFIG",
            ProgressConfig(
                heartbeat_interval=0.5, min_interval=0.2, message_max_chars=120
            ),
        )
        received: list[tuple[float, float, str]] = []

        async def on_progress(progress, _total, message):
            received.append((time.monotonic(), progress, message))

        script = (
            "await call_tool('setup_notebook', {'notebook_path': 'p.ipynb'})\n"
            "r = await call_tool('add_code_cell', {'notebook_path': 'p.ipynb', "
            "'source': 'import time\\nfor i in range(8):\\n"
            "    print(\\'tick\\', i, flush=True)\\n    time.sleep(0.3)'})\n"
            "await call_tool('shutdown_notebook', {'notebook_path': 'p.ipynb'})\n"
            "return r['status']"
        )
        transport = StreamableHttpTransport(
            _mcp_url(jp_http_port, jp_base_url),
            headers={"Authorization": f"Bearer {_token(jp_serverapp)}"},
        )

        async with Client(transport, timeout=120) as client:
            result = await client.call_tool(
                "execute", {"code": script}, progress_handler=on_progress
            )
            finished = time.monotonic()

        assert result.content[0].text == "ok"  # the script returns r['status']
        cell_messages = [m for _, _, m in received if m.startswith("p.ipynb cell 0")]
        assert any("tick" in m for m in cell_messages), received
        # Notifications stream in while the cell runs (~2.4s), not at the end.
        assert finished - received[0][0] > 0.8
        progresses = [p for _, p, _ in received]
        assert progresses == sorted(progresses)


def _script_value(result):
    """The value a Code Mode script returned.

    fastmcp wraps some return values as ``{"result": value}``; unwrap it.
    """
    data = json.loads(result.content[0].text)
    if isinstance(data, dict) and set(data) == {"result"}:
        return data["result"]
    return data


def _execute_args(notebook: str, *statements: str) -> dict:
    """A Code Mode script: set up ``notebook``, run statements, return ``out``."""
    lines = [f"await call_tool('setup_notebook', {{'notebook_path': {notebook!r}}})"]
    lines += list(statements)
    lines.append("return out")
    return {"code": "\n".join(lines)}


# A 60s cell that any OS can interrupt. On Windows, an interrupt only takes
# effect between Python bytecodes, so a single time.sleep(60) would run to
# completion there.
LONG_CELL = "import time\nfor _ in range(600):\n    time.sleep(0.1)"


def _cell(notebook: str, source: str) -> str:
    """Code Mode expression that runs ``source`` in a new cell of ``notebook``."""
    return (
        f"await call_tool('add_code_cell', "
        f"{{'notebook_path': {notebook!r}, 'source': {source!r}}})"
    )


class TestInterruptAndRestart:
    """Real kernels: timeouts, cancellation and explicit tools stop cells."""

    @pytest.fixture
    def jp_server_config(self):
        return _config(mcp_tools=[TOY_TOOL, *EXECUTION_TOOLS])

    @pytest.fixture
    def client_factory(self, jp_serverapp, jp_http_port, jp_base_url):
        def make(timeout=120):
            return Client(
                StreamableHttpTransport(
                    _mcp_url(jp_http_port, jp_base_url),
                    headers={"Authorization": f"Bearer {_token(jp_serverapp)}"},
                ),
                timeout=timeout,
            )

        return make

    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_timeout_interrupts_kernel_and_keeps_state(
        self, client_factory, monkeypatch
    ):
        short = dataclasses.replace(EXECUTION_CONFIG, default_timeout=2)
        for module in ("cell_operations", "execution_engine", "execution_utils"):
            monkeypatch.setattr(
                f"jupyter_mcp_executor.{module}.EXECUTION_CONFIG", short
            )

        args = _execute_args(
            "t.ipynb",
            "r1 = " + _cell("t.ipynb", "x = 41\n" + LONG_CELL),
            "r2 = " + _cell("t.ipynb", "x + 1"),
            "out = {'r1': [r1['status'], r1.get('error', '')], 'r2': r2['outputs']}",
        )
        start = time.monotonic()
        async with client_factory() as client:
            result = _script_value(await client.call_tool("execute", args))

        assert time.monotonic() - start < 30  # not the 60s the cell asked for
        status, error = result["r1"]
        assert status == "error"
        assert "the kernel was interrupted" in error
        # Kernel survived the interrupt with its state.
        assert result["r2"][0]["data"]["text/plain"] == "42"

    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_interrupt_tool_stops_running_cell(self, client_factory):
        async def run_long_cell():
            args = _execute_args(
                "i.ipynb",
                "r = " + _cell("i.ipynb", LONG_CELL),
                "out = [o.get('ename') for o in r['outputs']]",
            )
            async with client_factory() as client:
                return await client.call_tool("execute", args)

        start = time.monotonic()
        long_call = asyncio.create_task(run_long_cell())
        await asyncio.sleep(4)  # let the cell start
        async with client_factory() as other:
            interrupted = await other.call_tool(
                "execute",
                {
                    "code": "return await call_tool('interrupt_notebook_kernel', "
                    "{'notebook_path': 'i.ipynb'})"
                },
            )
        result = await asyncio.wait_for(long_call, timeout=30)

        assert "variables are kept" in interrupted.content[0].text
        assert _script_value(result) == ["KeyboardInterrupt"]
        assert time.monotonic() - start < 30

    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_restart_tool_clears_state_and_keeps_kernel(self, client_factory):
        args = _execute_args(
            "r.ipynb",
            "before = " + _cell("r.ipynb", "y = 5"),
            "restarted = await call_tool('restart_notebook_kernel', "
            "{'notebook_path': 'r.ipynb'})",
            "after = " + _cell("r.ipynb", "y"),
            "out = {'same_kernel': before['kernel_id'] == restarted['kernel_id'] "
            "== after['kernel_id'], 'after': [o.get('ename') for o in "
            "after['outputs']], 'message': restarted['message']}",
        )
        async with client_factory() as client:
            result = _script_value(await client.call_tool("execute", args))

        out = result
        assert out["same_kernel"] is True
        assert out["after"] == ["NameError"]  # y is gone after the restart
        assert "cleared" in out["message"]

    @pytest.mark.usefixtures("http_server", "mcp_extension")
    async def test_cancelled_request_interrupts_kernel(self, client_factory):
        setup = _execute_args("c.ipynb", "out = 'ready'")
        long_cell = _execute_args("c.ipynb", "out = " + _cell("c.ipynb", LONG_CELL))
        quick_cell = _execute_args(
            "c.ipynb", "out = (" + _cell("c.ipynb", "1 + 1") + ")['outputs']"
        )

        async with client_factory() as client:
            await client.call_tool("execute", setup)
        # This call gives up after 3s, which cancels the request server-side.
        async with client_factory() as impatient:
            with pytest.raises(Exception):  # noqa: B017, PT011 - client timeout error
                await impatient.call_tool("execute", long_cell, timeout=3)

        start = time.monotonic()
        async with client_factory() as client:
            result = await client.call_tool("execute", quick_cell)

        # Without the interrupt this would queue behind the 60s sleep.
        assert time.monotonic() - start < 20
        outputs = _script_value(result)
        assert outputs[-1]["data"]["text/plain"] == "2"
