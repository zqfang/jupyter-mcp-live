"""A kernel that cannot be started must surface as a readable error.

Before: _start_kernel_for_notebook() swallowed the reason and returned None,
so setup_notebook / switch_notebook_kernel failed with a pydantic
ValidationError ("kernel_id: Input should be a valid string"), and the
execution tools silently started a bare kernel without a Jupyter session.
"""

from unittest.mock import patch

import pytest
from tornado import web

from jupyter_mcp_live.execution_engine import ExecutionEngine
from jupyter_mcp_live.kernel_manager import KernelManager, KernelStartError
from tests.test_kernel_reuse import FakeServerApp

_KM = "jupyter_mcp_live.kernel_manager"


class RejectingContentsManager:
    """What FileContentsManager answers for a path outside root_dir."""

    def get(self, path, content=True, type=None):
        raise web.HTTPError(404, f"No such file or directory: {path}")


class RecordingKernelManager:
    """Mapping kernel manager that records bare start_kernel() calls."""

    def __init__(self, inner):
        self._inner = inner
        self.started = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __contains__(self, kernel_id):
        return kernel_id in self._inner

    async def start_kernel(self, **kwargs):
        self.started.append(kwargs)
        return "bare-kernel"


class _Adapter:
    """The minimum execute_and_update_cell needs before resolving a kernel."""

    is_collaborative = False

    def __init__(self, path):
        self.notebook_path = path


@pytest.fixture
def notebook(tmp_path):
    path = tmp_path / "nb.ipynb"
    path.write_text("{}")
    return str(path)


@pytest.fixture
def rejecting_app():
    app = FakeServerApp()
    app.contents_manager = RejectingContentsManager()
    app.kernel_manager = RecordingKernelManager(app.kernel_manager)
    return app


def _patched(app):
    return (
        patch(f"{_KM}.get_server_context", return_value=app),
        patch(f"{_KM}.require_server_context", return_value=app),
        patch(
            "jupyter_mcp_live.execution_engine.get_server_context",
            return_value=app,
        ),
    )


@pytest.mark.asyncio
async def test_setup_notebook_explains_why_no_kernel_started(notebook, rejecting_app):
    a, b, c = _patched(rejecting_app)
    with a, b, c, pytest.raises(KernelStartError, match="contents manager"):
        await KernelManager().setup_notebook(notebook)


@pytest.mark.asyncio
async def test_execution_never_starts_a_bare_kernel(notebook, rejecting_app):
    engine = ExecutionEngine(kernel_service=KernelManager())
    a, b, c = _patched(rejecting_app)
    with a, b, c, pytest.raises(KernelStartError):
        await engine.execute_and_update_cell(_Adapter(notebook), 0, "1 + 1")

    assert rejecting_app.kernel_manager.started == []


@pytest.mark.asyncio
async def test_switch_rejects_unknown_kernel_before_stopping_the_old_one(notebook):
    app = FakeServerApp(
        sessions=[
            {
                "id": "s1",
                "path": notebook,
                "name": "nb.ipynb",
                "kernel": {"id": "old", "name": "python3", "connections": 1},
            }
        ],
        live_ids={"old"},
    )
    a, b, c = _patched(app)
    with a, b, c, pytest.raises(ValueError, match="Available kernels"):
        await KernelManager().switch_kernel(notebook, "no-such-kernel")

    assert app.kernel_manager.shutdown_calls == []
    assert len(await app.session_manager.list_sessions()) == 1
