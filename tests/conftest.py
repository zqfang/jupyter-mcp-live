"""Shared pytest configuration for the unit tests.

Markers are declared in pytest.ini. Tests under tests/integration/ run a real
Jupyter Server with pytest-jupyter's fixtures (see its conftest.py) and are
marked ``integration`` automatically.
"""

pytest_plugins = ["pytest_asyncio"]
