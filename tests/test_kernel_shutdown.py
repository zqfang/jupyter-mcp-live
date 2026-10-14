"""shutdown_notebook / switch_notebook_kernel against jupyter_server semantics.

jupyter_server's SessionManager.delete_session() always shuts the session's
kernel down. The fake below does the same, unlike the one in
test_kernel_reuse.py, so these tests see what a real server does.
"""

from unittest.mock import patch

import pytest

from jupyter_mcp_executor.kernel_manager import KernelManager
from tests.test_kernel_reuse import FakeServerApp, FakeSessionManager

_KM = "jupyter_mcp_executor.kernel_manager"


class ClosingSessionManager(FakeSessionManager):
    """delete_session() shuts the kernel down, as in jupyter_server."""

    def __init__(self, kernel_manager, sessions=None):
        super().__init__(sessions)
        self._kernel_manager = kernel_manager

    async def delete_session(self, session_id):
        session = next(s for s in self.sessions if s["id"] == session_id)
        await self._kernel_manager.shutdown_kernel(session["kernel"]["id"])
        await super().delete_session(session_id)


@pytest.fixture
def notebook(tmp_path):
    path = tmp_path / "nb.ipynb"
    path.write_text("{}")
    return str(path)


@pytest.fixture
def app(notebook):
    """A notebook open in JupyterLab: a session the MCP tools never tracked."""
    app = FakeServerApp(live_ids={"lab-kernel"})
    app.session_manager = ClosingSessionManager(
        app.kernel_manager,
        [
            {
                "id": "s1",
                "path": notebook,
                "name": "nb.ipynb",
                "kernel": {"id": "lab-kernel", "name": "python3", "connections": 1},
            }
        ],
    )
    return app


@pytest.fixture
def manager(app):
    with (
        patch(f"{_KM}.get_server_context", return_value=app),
        patch(f"{_KM}.require_server_context", return_value=app),
        patch(f"{_KM}.to_api_path", return_value="nb.ipynb"),
    ):
        mgr = KernelManager()
        yield mgr
        mgr.kernel_tracker._kernels.clear()


@pytest.mark.asyncio
async def test_shutdown_stops_the_session_kernel_once_and_names_it(
    manager, app, notebook
):
    result = await manager.shutdown_notebook(notebook)

    assert app.kernel_manager.shutdown_calls == ["lab-kernel"]
    assert result.kernel_id == "lab-kernel"
    assert await app.session_manager.list_sessions() == []


@pytest.mark.asyncio
async def test_shutdown_without_kernel_keeps_the_session_and_kernel(
    manager, app, notebook
):
    """Deleting the session would kill the kernel, so it must be kept."""
    result = await manager.shutdown_notebook(notebook, shutdown_kernel=False)

    assert app.kernel_manager.shutdown_calls == []
    assert len(await app.session_manager.list_sessions()) == 1
    assert result.kernel_id == "lab-kernel"
    assert "running" in result.message
    assert manager.kernel_tracker.get(notebook) is None


@pytest.mark.asyncio
async def test_shutdown_also_stops_a_tracked_kernel_without_session(
    manager, app, notebook
):
    app.kernel_manager.live.add("orphan")
    manager.kernel_tracker.track(notebook, "orphan")

    await manager.shutdown_notebook(notebook)

    assert sorted(app.kernel_manager.shutdown_calls) == ["lab-kernel", "orphan"]


@pytest.mark.asyncio
async def test_switch_stops_the_old_kernel_once_and_reports_it(manager, app, notebook):
    result = await manager.switch_kernel(notebook, "python3")

    assert app.kernel_manager.shutdown_calls == ["lab-kernel"]
    assert result.previous_kernel_id == "lab-kernel"
    assert result.kernel_id != "lab-kernel"
