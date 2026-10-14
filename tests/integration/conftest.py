"""Fixtures for tests that run a real Jupyter Server with the extension.

pytest-jupyter's fixtures (and its async test runner hook) are imported here
rather than enabled globally, so they only apply to this directory and do not
interfere with the pytest-asyncio based unit tests elsewhere.
"""

import contextlib
import gc
from pathlib import Path

import pytest
from pytest_jupyter.jupyter_server import *

from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.server_context import set_server_context
from jupyter_mcp_executor.service_container import container

_HERE = Path(__file__).parent


# The real server stack leaks on its own: FastMCP leaves an anyio memory
# stream per Code Mode tool call open, and the YDoc stack opens SQLite
# connections it never closes. pytest.ini makes leaks errors elsewhere.
_UPSTREAM_LEAKS = (
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
)


def pytest_collection_modifyitems(items):
    """Mark every test in this directory ``integration`` (CI runs them apart)."""
    for item in items:
        if item.path.is_relative_to(_HERE):
            item.add_marker(pytest.mark.integration)
            for mark in _UPSTREAM_LEAKS:
                item.add_marker(mark)


@pytest.fixture(autouse=True)
def _close_signature_store(request):
    """Close the test server's notebook signature store after the test.

    jupyter_server never closes its contents manager's notary database
    (ResourceWarning: unclosed database) when the server stops.
    """
    serverapp = None
    if "jp_serverapp" in request.fixturenames:
        serverapp = request.getfixturevalue("jp_serverapp")
    yield
    if serverapp is not None:
        with contextlib.suppress(Exception):
            serverapp.contents_manager.notary.store.close()


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
    # Collect the server stack's leaked objects now, under this test's
    # _UPSTREAM_LEAKS filters: collected later, during a unit test, their
    # ResourceWarnings would fail that test.
    gc.collect()
