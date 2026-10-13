"""Pytest configuration and fixtures for jupyter-server-docs-mcp tests.

This file also injects a lightweight stub for
``jupyter_mcp_executor.query_service`` to avoid a circular import during
module import in tests. The stub provides a minimal ``QueryService``
implementation that delegates to safe modules and the active server
context, without importing ``KernelManager``.
"""

import asyncio
import sys
import types
import tempfile
from pathlib import Path

import pytest
from tornado.web import Application

from jupyter_mcp_executor.extension import MCPExtensionApp
from jupyter_mcp_executor.mcp_server import MCPServer

# ---------------------------------------------------------------------------
# Install a stub for jupyter_mcp_executor.query_service BEFORE tests import
# modules that would otherwise trigger a circular import between
# kernel_manager <-> query_service.
# ---------------------------------------------------------------------------

if "jupyter_mcp_executor.query_service" not in sys.modules:
    stub = types.ModuleType("jupyter_mcp_executor.query_service")

    # Safe imports (do not import KernelManager)
    from jupyter_mcp_executor.cell_images import CellImages
    from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter
    from jupyter_mcp_executor.notebook_models import CellIdentifier
    from jupyter_mcp_executor.output_filter import OutputFilter
    from jupyter_mcp_executor.path_utils import normalize_path
    from jupyter_mcp_executor.server_context import require_server_context

    class QueryService:  # type: ignore
        """Minimal QueryService stub used in tests to break import cycles."""

        async def view_notebook(self, notebook_path: str):
            notebook_path = normalize_path(notebook_path)
            adapter = await get_notebook_adapter(notebook_path)
            cells = await adapter.get_all_cells()
            return OutputFilter.filter_cells(
                [
                    {
                        "cell_type": c.cell_type,
                        "source": c.source,
                        "metadata": c.metadata,
                        "execution_count": c.execution_count,
                        "outputs": c.outputs,
                        "id": c.cell_id,
                    }
                    for c in cells
                ]
            )

        @staticmethod
        async def _resolve_position(adapter, identifier: CellIdentifier) -> int:
            if identifier.position_index is not None:
                return identifier.position_index
            if identifier.cell_id is not None:
                result = await adapter.find_cell_by_id(identifier.cell_id)
                if result is None:
                    raise ValueError(f"Cell with ID {identifier.cell_id} not found")
                return result[1]
            result = await adapter.find_cell_by_execution_count(
                identifier.execution_count
            )
            if result is None:
                raise ValueError(
                    f"Cell with execution_count={identifier.execution_count} not found"
                )
            return result[1]

        async def view_cell_images(
            self, notebook_path: str, identifier: CellIdentifier
        ) -> CellImages:
            notebook_path = normalize_path(notebook_path)
            adapter = await get_notebook_adapter(notebook_path)
            position = await self._resolve_position(adapter, identifier)
            cell = await adapter.read_cell(position)
            return CellImages.from_cell(cell, position)

        async def read_cell(self, notebook_path: str, identifier: CellIdentifier):
            notebook_path = normalize_path(notebook_path)
            adapter = await get_notebook_adapter(notebook_path)
            position = await self._resolve_position(adapter, identifier)
            cell = await adapter.read_cell(position)
            cell_dict = {
                "cell_type": cell.cell_type,
                "source": cell.source,
                "metadata": cell.metadata,
                "execution_count": cell.execution_count,
                "outputs": cell.outputs,
                "id": cell.cell_id,
                "position": position,
            }
            if cell.cell_type == "code":
                cell_dict["outputs"] = OutputFilter.filter_outputs(cell.outputs)
            return cell_dict

        async def list_sessions(self):
            """Expose list_sessions used by KernelManager._find_session_for_notebook."""
            serverapp = require_server_context()
            sessions = await serverapp.session_manager.list_sessions()
            return [dict(s) for s in sessions]

    # Expose QueryService in the stub module
    stub.QueryService = QueryService
    sys.modules["jupyter_mcp_executor.query_service"] = stub


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mcp_extension():
    """Create an MCP extension instance for testing."""
    extension = MCPExtensionApp()
    extension.mcp_port = 3099  # Use high port to avoid conflicts
    return extension


@pytest.fixture
def mock_serverapp():
    """Create a mock Jupyter Server app for testing."""

    class MockServerApp:
        def __init__(self):
            self.log = MockLogger()
            self.web_app = Application()  # Add required web_app attribute

    class MockLogger:
        def info(self, msg):
            print(f"INFO: {msg}")

        def error(self, msg):
            print(f"ERROR: {msg}")

        def warning(self, msg):
            print(f"WARNING: {msg}")

    return MockServerApp()


@pytest.fixture
def mcp_server_simple():
    """Create a simple MCP server for testing."""
    return MCPServer(port=3097, log_level="WARNING")  # Use WARNING to reduce test noise


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark test as integration test")


# Auto-use asyncio for async tests
pytest_plugins = ["pytest_asyncio"]
