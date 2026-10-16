"""Shared pytest configuration for the unit tests.

Markers are declared in pytest.ini. Tests under tests/integration/ run a real
Jupyter Server with pytest-jupyter's fixtures (see its conftest.py) and are
marked ``integration`` automatically.
"""

import pytest

from jupyter_mcp_live.server_context import set_server_context

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture(autouse=True)
def _no_leftover_server_context():
    """Start and end every test without a server context.

    The context is process-wide; one left behind by an earlier test would
    make normalize_path() check later tests' notebooks against its root dir.
    """
    set_server_context(None)
    yield
    set_server_context(None)
