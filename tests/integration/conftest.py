"""Fixtures for tests that run a real Jupyter Server with the extension.

pytest-jupyter's fixtures (and its async test runner hook) are imported here
rather than enabled globally, so they only apply to this directory and do not
interfere with the pytest-asyncio based unit tests elsewhere.
"""

import contextlib
from pathlib import Path

import pytest
from pytest_jupyter.jupyter_server import *

from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.server_context import set_server_context
from jupyter_mcp_executor.service_container import container

_HERE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    """Mark every test in this directory ``integration`` (CI runs them apart)."""
    for item in items:
        if item.path.is_relative_to(_HERE):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _reset_process_state():
    """Close cached kernel clients and clear the process-wide singletons.

    The execution engine caches a real client per kernel; left open, its ZMQ
    sockets outlive the test's server (ResourceWarning: unclosed socket) and
    its kernel IDs leak into later tests.
    """
    yield
    engine = container.execution_engine
    for client in engine._clients.values():
        with contextlib.suppress(Exception):
            client.stop_channels()
    engine._clients.clear()
    engine._kernel_locks.clear()
    get_kernel_tracker()._kernels.clear()
    set_server_context(None)
