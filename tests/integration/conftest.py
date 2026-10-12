"""Fixtures for tests that run a real Jupyter Server with the extension.

pytest-jupyter's fixtures (and its async test runner hook) are imported here
rather than enabled globally, so they only apply to this directory and do not
interfere with the pytest-asyncio based unit tests elsewhere.
"""

from pytest_jupyter.jupyter_server import *  # noqa: F403
