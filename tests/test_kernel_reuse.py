"""Regression tests for the rule that the agent and JupyterLab share one kernel.

Each test here fails against the pre-fix code:

A. a dead kernel raised tornado.web.HTTPError(404), not KeyError, so every
   "kernel died -> fall back" guard was dead code and the 404 escaped to the caller;
B. setup_notebook(force_new_kernel=True) created an extra session per call instead of
   replacing the old one, because SessionManager.create_session does not dedupe by path;
C. with duplicate sessions present, the first one out of list_sessions() won, so this
   client and a JupyterLab tab could settle on different kernels.
"""

from unittest.mock import patch

import pytest
from tornado import web

from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.kernel_utils import is_kernel_alive


class FakeKernelManager:
    """Mimics jupyter_server's MappingKernelManager error contract."""

    def __init__(self, live_ids=()):
        self.live = set(live_ids)
        self.shutdown_calls = []

    def get_kernel(self, kernel_id):
        if kernel_id not in self.live:
            # This is the real behaviour: jupyter_server's MappingKernelManager
            # overrides _check_kernel_id to raise a 404, NOT a KeyError.
            raise web.HTTPError(404, "Kernel does not exist: %s" % kernel_id)
        return type("K", (), {"kernel_name": "python3"})()

    async def shutdown_kernel(self, kernel_id):
        self.shutdown_calls.append(kernel_id)
        self.live.discard(kernel_id)


class FakeSessionManager:
    """SessionManager that, like the real one, does not dedupe sessions by path."""

    def __init__(self, sessions=None):
        self.sessions = list(sessions or [])
        self._n = 0

    async def list_sessions(self):
        return list(self.sessions)

    async def create_session(self, path, name, type, kernel_name, kernel_id=None):
        self._n += 1
        session = {
            "id": f"session-{self._n}",
            "path": path,
            "name": name,
            "kernel": {"id": f"kernel-{self._n}", "name": kernel_name, "connections": 0},
        }
        self.sessions.append(session)
        return session

    async def delete_session(self, session_id):
        self.sessions = [s for s in self.sessions if s["id"] != session_id]


class FakeKernelSpecManager:
    def get_all_specs(self):
        return {"python3": {"spec": {"language": "python", "display_name": "Python 3"}}}


class FakeServerApp:
    def __init__(self, sessions=None, live_ids=()):
        self.session_manager = FakeSessionManager(sessions)
        self.kernel_manager = FakeKernelManager(live_ids)
        self.kernel_spec_manager = FakeKernelSpecManager()
        self.root_dir = "/"
        self.contents_manager = type("CM", (), {"get": lambda self, p, type=None: {}})()


def test_dead_kernel_reports_not_alive_instead_of_raising_404():
    """Defect A: the liveness check must swallow jupyter_server's 404."""
    app = FakeServerApp(live_ids={"live-kernel"})
    assert is_kernel_alive(app, "live-kernel") is True
    assert is_kernel_alive(app, "dead-kernel") is False       # raised HTTPError before
    assert is_kernel_alive(app, None) is False
    assert is_kernel_alive(None, "live-kernel") is False


@pytest.mark.asyncio
async def test_dead_tracked_kernel_falls_back_to_the_live_session(tmp_path):
    """Defect A, end to end: a stale tracked id must not 404 the caller."""
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}")
    path = str(nb)
    app = FakeServerApp(
        sessions=[{"id": "s1", "path": path, "name": "nb.ipynb",
                   "kernel": {"id": "live-kernel", "name": "python3", "connections": 1}}],
        live_ids={"live-kernel"},
    )
    mgr = KernelManager()
    mgr.kernel_tracker.track(path, "dead-kernel")

    with patch("jupyter_mcp_executor.kernel_manager.get_server_context", return_value=app):
        kernel_id, started_new = await mgr.get_kernel_for_notebook(path)

    assert kernel_id == "live-kernel"
    assert started_new is False


@pytest.mark.asyncio
async def test_force_new_kernel_replaces_the_session_instead_of_adding_one(tmp_path):
    """Defect B: one notebook must never end up with two sessions."""
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}")
    path = str(nb)
    app = FakeServerApp(
        sessions=[{"id": "s1", "path": path, "name": "nb.ipynb",
                   "kernel": {"id": "old-kernel", "name": "python3", "connections": 0}}],
        live_ids={"old-kernel"},
    )
    mgr = KernelManager()
    mgr.kernel_tracker.track(path, "old-kernel")

    with patch("jupyter_mcp_executor.kernel_manager.get_server_context", return_value=app), \
         patch("jupyter_mcp_executor.kernel_manager.require_server_context", return_value=app), \
         patch("jupyter_mcp_executor.kernel_manager.to_api_path", return_value="nb.ipynb"):
        await mgr.setup_notebook(path, kernel_name="python3", force_new_kernel=True)

    sessions = await app.session_manager.list_sessions()
    assert len(sessions) == 1, f"force_new_kernel leaked a session: {sessions}"
    assert "old-kernel" in app.kernel_manager.shutdown_calls, "old kernel left running"


@pytest.mark.asyncio
async def test_duplicate_sessions_resolve_to_the_one_a_jupyterlab_tab_is_on(tmp_path):
    """Defect C: the human's connected kernel wins, not list order."""
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}")
    path = str(nb)
    app = FakeServerApp(
        sessions=[
            {"id": "s1", "path": path, "name": "nb.ipynb",
             "kernel": {"id": "orphan", "name": "python3", "connections": 0,
                        "last_activity": "2026-01-01T00:00:00Z"}},
            {"id": "s2", "path": path, "name": "nb.ipynb",
             "kernel": {"id": "jupyterlab-tab", "name": "python3", "connections": 1,
                        "last_activity": "2026-01-01T00:00:00Z"}},
        ],
        live_ids={"orphan", "jupyterlab-tab"},
    )
    mgr = KernelManager()

    with patch("jupyter_mcp_executor.kernel_manager.get_server_context", return_value=app):
        session = await mgr._find_session_for_notebook(path)
    assert session["kernel"]["id"] == "jupyterlab-tab"      # was "orphan" (list order)

    # ...unless this client already has a kernel for the notebook, which stays stable.
    mgr.kernel_tracker.track(path, "orphan")
    with patch("jupyter_mcp_executor.kernel_manager.get_server_context", return_value=app):
        session = await mgr._find_session_for_notebook(path)
    assert session["kernel"]["id"] == "orphan"
