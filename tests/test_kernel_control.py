"""Tests for interrupting and restarting a notebook's kernel."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest

from jupyter_mcp_executor import execution_tools
from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.notebook_models import KernelActionResult
from jupyter_mcp_executor.service_container import container
from tests.test_kernel_reuse import FakeKernelManager, FakeServerApp


class ControllableKernelManager(FakeKernelManager):
    """Fake MappingKernelManager that records interrupts and restarts."""

    def __init__(self, live_ids=()):
        super().__init__(live_ids)
        self.interrupts: list[str] = []
        self.restarts: list[str] = []

    def interrupt_kernel(self, kernel_id):  # sync, like jupyter_server's wrapper
        self.interrupts.append(kernel_id)

    async def restart_kernel(self, kernel_id, now=False):  # noqa: ARG002
        self.restarts.append(kernel_id)


def _app(path=None, session_kernel=None, live_ids=()):
    sessions = []
    if session_kernel:
        sessions.append(
            {
                "id": "s1",
                "path": path,
                "name": "nb.ipynb",
                "kernel": {"id": session_kernel, "name": "python3", "connections": 1},
            }
        )
    app = FakeServerApp(sessions=sessions)
    app.kernel_manager = ControllableKernelManager(live_ids)
    return app


def _patched(app):
    """Point both server-context helpers used by KernelManager at ``app``."""
    stack = ExitStack()
    for name in ("get_server_context", "require_server_context"):
        stack.enter_context(
            patch(f"jupyter_mcp_executor.kernel_manager.{name}", return_value=app)
        )
    return stack


@pytest.fixture
def notebook(tmp_path):
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}")
    return str(nb)


@pytest.fixture
def manager(notebook):
    mgr = KernelManager()
    yield mgr
    mgr.kernel_tracker.untrack(notebook)


class TestFindKernelForNotebook:
    """Lookup only: tracked kernel, then a Jupyter session; never starts one."""

    @pytest.mark.asyncio
    async def test_returns_tracked_live_kernel(self, notebook, manager):
        app = _app(live_ids={"tracked"})
        manager.kernel_tracker.track(notebook, "tracked")

        with _patched(app):
            assert await manager.find_kernel_for_notebook(notebook) == "tracked"

    @pytest.mark.asyncio
    async def test_falls_back_to_session_kernel(self, notebook, manager):
        app = _app(notebook, session_kernel="from-session", live_ids={"from-session"})
        manager.kernel_tracker.track(notebook, "dead")

        with _patched(app):
            assert await manager.find_kernel_for_notebook(notebook) == "from-session"

    @pytest.mark.asyncio
    async def test_returns_none_without_starting_a_kernel(self, notebook, manager):
        app = _app()

        with _patched(app):
            assert await manager.find_kernel_for_notebook(notebook) is None

        assert app.session_manager.sessions == []


class TestInterruptAndRestart:
    @pytest.mark.asyncio
    async def test_interrupt_sends_interrupt_to_notebook_kernel(
        self, notebook, manager
    ):
        app = _app(live_ids={"k1"})
        manager.kernel_tracker.track(notebook, "k1")

        with _patched(app):
            result = await manager.interrupt_notebook_kernel(notebook)

        assert app.kernel_manager.interrupts == ["k1"]
        assert isinstance(result, KernelActionResult)
        assert result.kernel_id == "k1"
        assert "kept" in result.message

    @pytest.mark.asyncio
    async def test_restart_restarts_same_kernel(self, notebook, manager):
        app = _app(live_ids={"k1"})
        manager.kernel_tracker.track(notebook, "k1")

        with _patched(app):
            result = await manager.restart_notebook_kernel(notebook)

        assert app.kernel_manager.restarts == ["k1"]
        assert result.kernel_id == "k1"
        assert "cleared" in result.message
        # Same kernel id, so tracking survives the restart.
        assert manager.kernel_tracker.get(notebook) == "k1"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["interrupt", "restart"])
    async def test_no_running_kernel_raises_with_hint(self, notebook, manager, action):
        app = _app()

        with _patched(app), pytest.raises(ValueError, match="setup_notebook"):
            await getattr(manager, f"{action}_notebook_kernel")(notebook)

        assert app.kernel_manager.interrupts == []
        assert app.kernel_manager.restarts == []


class TestKernelControlTools:
    @pytest.mark.asyncio
    async def test_restart_tool_forgets_cached_kernel_client(
        self, notebook, monkeypatch
    ):
        forgotten = []

        class FakeKernelService:
            async def restart_notebook_kernel(self, path):
                return KernelActionResult(kernel_id="k1", message=f"restarted {path}")

        class FakeEngine:
            def forget_kernel_client(self, kernel_id):
                forgotten.append(kernel_id)

        # Patch the engine first: reading the original builds the real engine
        # from container.kernel_manager, which must not be the fake yet.
        monkeypatch.setattr(container, "execution_engine", FakeEngine())
        monkeypatch.setattr(container, "kernel_manager", FakeKernelService())

        result = await execution_tools.restart_notebook_kernel(notebook)

        assert result["kernel_id"] == "k1"
        assert forgotten == ["k1"]

    @pytest.mark.asyncio
    async def test_interrupt_tool_returns_kernel_and_message(
        self, notebook, monkeypatch
    ):
        class FakeKernelService:
            async def interrupt_notebook_kernel(self, path):
                return KernelActionResult(kernel_id="k1", message=f"interrupted {path}")

        monkeypatch.setattr(container, "kernel_manager", FakeKernelService())

        result = await execution_tools.interrupt_notebook_kernel(notebook)

        assert result == {"kernel_id": "k1", "message": f"interrupted {notebook}"}

    def test_tools_are_exported(self):
        assert "interrupt_notebook_kernel" in execution_tools.__all__
        assert "restart_notebook_kernel" in execution_tools.__all__
