"""Test notebook execution tools functionality - refactored for modular architecture."""

import asyncio
import dataclasses
from datetime import datetime
from pathlib import Path
from queue import Empty

import nbformat
import pytest
from jupyter_client.session import Session
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from jupyter_mcp_executor.execution_progress import ExecutionProgress
from jupyter_mcp_executor.execution_tools import (
    add_code_cell,
    add_markdown_cell,
    delete_cell,
    edit_code_cell,
    edit_markdown_cell,
    execute_cell,
    get_server_context,
    list_notebook_kernels,
    read_cell,
    set_server_context,
    setup_notebook,
    shutdown_notebook,
    switch_notebook_kernel,
    view_notebook,
)
from jupyter_mcp_executor.execution_utils import execute_code_on_kernel

# Import from new modular structure
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG, ProgressConfig
from jupyter_mcp_executor.notebook_io import read_notebook, write_notebook
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.path_utils import normalize_path, to_api_path
from jupyter_mcp_executor.server_context import _server_context
from jupyter_mcp_executor.service_container import container


class MockKernelManager:
    """Mock kernel manager for testing."""

    def __init__(self):
        self.kernels = {}
        self._next_id = 0

    async def start_kernel(self, kernel_name="python3"):
        """Mock start kernel."""
        kernel_id = f"kernel-{self._next_id}"
        self._next_id += 1
        self.kernels[kernel_id] = MockKernel(kernel_id, kernel_name)
        return kernel_id

    async def shutdown_kernel(self, kernel_id):
        """Mock shutdown kernel."""
        if kernel_id in self.kernels:
            del self.kernels[kernel_id]

    def __contains__(self, kernel_id):
        return kernel_id in self.kernels

    def get_kernel(self, kernel_id):
        """Mock get kernel."""
        if kernel_id not in self.kernels:
            msg = f"Kernel {kernel_id} not found"
            raise KeyError(msg)
        return self.kernels[kernel_id]

    def list_kernel_ids(self):
        """Mock list kernel IDs."""
        return list(self.kernels.keys())


class MockKernel:
    """Mock kernel for testing."""

    def __init__(self, kernel_id, kernel_name="python3"):
        self.kernel_id = kernel_id
        self.kernel_name = kernel_name
        self.last_activity = datetime.now()
        self.execution_state = "idle"
        self.connections = 0
        self.session = Session()
        self._client = MockKernelClient()

    def client(self, **_kwargs):
        """Return a mock client."""
        return self._client


class MockKernelClient:
    """Mock kernel client for testing."""

    def __init__(self):
        self.channels_started = False
        self.execution_count = 1
        self._current_msg_id = None
        self._shell_messages = []

    def start_channels(self):
        """Mock start channels."""
        self.channels_started = True

    def stop_channels(self):
        """Mock stop channels."""
        self.channels_started = False

    async def wait_for_ready(self, timeout=10):
        """Mock wait for ready."""
        await asyncio.sleep(0.01)

    def execute(self, _code, **_kwargs):
        """Mock execute - prepares shell reply with execution_count."""
        msg_id = f"msg-id-{self.execution_count}"
        self._current_msg_id = msg_id

        # Queue the execute_reply for shell channel
        self._shell_messages.append(
            {
                "header": {
                    "msg_type": "execute_reply",
                    "msg_id": f"reply-{self.execution_count}",
                },
                "parent_header": {"msg_id": msg_id},
                "content": {"status": "ok", "execution_count": self.execution_count},
            }
        )

        self.execution_count += 1
        return msg_id

    async def get_shell_msg(self, timeout=None):
        """Mock get shell message - returns execute_reply."""
        await asyncio.sleep(0.01)

        if self._shell_messages:
            return self._shell_messages.pop(0)

        msg = "No shell messages available"
        raise asyncio.TimeoutError(msg)

    async def get_iopub_msg(self, timeout=None):
        """Mock get iopub message."""
        await asyncio.sleep(0.01)

        # Simulate execution complete
        return {
            "header": {"msg_type": "status", "msg_id": "msg-id-456"},
            "parent_header": {"msg_id": self._current_msg_id or "msg-id-123"},
            "content": {"execution_state": "idle"},
        }


class MockKernelSpecManager:
    """Mock kernel spec manager."""

    def get_all_specs(self):
        """Return mock kernel specs."""
        return {
            "python3": {"spec": {"display_name": "Python 3", "language": "python"}},
            "ir": {"spec": {"display_name": "R", "language": "R"}},
        }


class MockServerApp:
    """Mock server app for testing."""

    def __init__(self, root_dir):
        self.kernel_manager = MockKernelManager()
        self.kernel_spec_manager = MockKernelSpecManager()
        self.session_manager = MockSessionManager(kernel_manager=self.kernel_manager)
        # Notebooks must be inside the root (normalize_path rejects others)
        self.root_dir = str(root_dir)
        self.contents_manager = MockContentsManager()


class MockContentsManager:
    """Minimal contents manager to satisfy KernelManager preflight checks."""

    def get(self, path, content=True, type=None):
        # Return a minimal notebook model dict similar to Jupyter contents API
        return {"type": "notebook", "path": path, "name": Path(path).name}


class MockSessionManager:
    """Mock session manager."""

    def __init__(self, kernel_manager=None):
        """Initialize with empty sessions list and optional kernel manager.

        If a kernel_manager is provided, create_session will start kernels via
        that manager to keep kernel IDs consistent with ExecutionEngine.
        """
        self.sessions = []
        self.kernel_manager = kernel_manager

    async def list_sessions(self):
        """Mock list sessions - returns list like real Jupyter Server."""
        return list(self.sessions)

    async def create_session(self, path, name, type, kernel_id=None, kernel_name=None):
        """Mock create session - mimics real Jupyter Server API.

        Args:
            path: Notebook path
            name: Notebook name
            type: Session type (usually "notebook")
            kernel_id: Existing kernel ID (if provided)
            kernel_name: Kernel name (if starting new kernel)

        Returns:
            Session dict
        """
        # Start a kernel via kernel_manager if available and kernel_id not provided
        if kernel_id is None and self.kernel_manager is not None:
            kernel_id = await self.kernel_manager.start_kernel(
                kernel_name=kernel_name or "python3"
            )

        session = {
            "id": f"session-{len(self.sessions)}",
            "path": path,
            "name": name,
            "type": type,
            "kernel": {
                "id": kernel_id or f"kernel-{len(self.sessions)}",
                "name": kernel_name or "python3",
                "last_activity": datetime.now().isoformat(),
                "execution_state": "idle",
                "connections": 1,
            },
        }
        self.sessions.append(session)
        return session

    async def delete_session(self, session_id):
        """Mock delete session - like jupyter_server, also shuts the kernel down.

        Args:
            session_id: ID of session to delete
        """
        for session in self.sessions:
            if session["id"] == session_id and self.kernel_manager is not None:
                await self.kernel_manager.shutdown_kernel(session["kernel"]["id"])
        self.sessions = [s for s in self.sessions if s["id"] != session_id]

    def add_session(self, notebook_path, kernel_id, kernel_name="python3"):
        """Add a mock session for testing (legacy helper method)."""
        session = {
            "id": f"session-{len(self.sessions)}",
            "path": notebook_path,
            "name": Path(notebook_path).name,
            "type": "notebook",
            "kernel": {
                "id": kernel_id,
                "name": kernel_name,
                "last_activity": datetime.now().isoformat(),
                "execution_state": "idle",
                "connections": 1,
            },
        }
        self.sessions.append(session)
        return session


@pytest.fixture
def temp_notebook(tmp_path):
    """Create a temporary notebook for testing."""
    nb_path = tmp_path / "test.ipynb"
    nb = new_notebook(cells=[])
    with nb_path.open("w") as f:
        nbformat.write(nb, f)
    return str(nb_path)


@pytest.fixture
def mock_serverapp(tmp_path):
    """Create a mock server app rooted at the test's tmp dir (temp_notebook)."""
    return MockServerApp(tmp_path)


@pytest.fixture(autouse=True)
def reset_context():
    """Reset global context before each test."""

    def _reset():
        _server_context["serverapp"] = None
        get_kernel_tracker()._kernels = {}
        # Kernel IDs repeat across tests (each MockServerApp starts at
        # kernel-0), so cached clients from a previous test must not leak in.
        container.execution_engine._clients = {}
        container.execution_engine._kernel_locks = {}

    _reset()
    yield
    _reset()


class TestHelperFunctions:
    """Test helper functions from new modules."""

    def test_normalize_path(self):
        """Test path normalization."""
        path = "test.ipynb"
        normalized = normalize_path(path)
        assert Path(normalized).is_absolute()
        assert normalized.endswith("test.ipynb")

    def test_normalize_path_empty_raises(self):
        """Test that empty path raises ValueError."""
        with pytest.raises(ValueError, match="path cannot be empty"):
            normalize_path("")

    def test_to_api_path(self, mock_serverapp):
        """Test converting absolute paths to API paths."""
        set_server_context(mock_serverapp)

        # Test path under root_dir
        root_dir = mock_serverapp.root_dir
        absolute_path = str(Path(root_dir) / "notebook.ipynb")
        api_path = to_api_path(absolute_path)
        assert api_path == "notebook.ipynb"

        # Test path in subdirectory
        absolute_path = str(Path(root_dir) / "data" / "analysis.ipynb")
        api_path = to_api_path(absolute_path)
        assert api_path == "data/analysis.ipynb"

        # Test path outside root_dir (outside returns absolute; compare filename)
        outside_path = "/tmp/external.ipynb"
        api_path = to_api_path(outside_path)
        assert Path(api_path).name == "external.ipynb"

    def test_to_api_path_under_symlinked_root(self, mock_serverapp, tmp_path):
        """normalize_path() resolves symlinks; Jupyter keeps root_dir as given.

        Comparing the two unresolved made every notebook look outside the
        root: no Jupyter session, and another YDoc room than JupyterLab's.
        """
        real = tmp_path / "real"
        (real / "sub").mkdir(parents=True)
        link = tmp_path / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlinks here")
        mock_serverapp.root_dir = str(link)
        set_server_context(mock_serverapp)

        resolved = normalize_path("sub/nb.ipynb")

        assert resolved == str((real / "sub" / "nb.ipynb").resolve())
        assert to_api_path(resolved) == "sub/nb.ipynb"

    def test_read_write_notebook(self, temp_notebook):
        """Test reading and writing notebooks."""
        # Read the notebook
        nb = read_notebook(temp_notebook)
        assert nb is not None
        assert len(nb.cells) == 0

        # Add a cell
        nb.cells.append(new_code_cell("print('hello')"))

        # Write the notebook
        write_notebook(temp_notebook, nb)

        # Read it again
        nb2 = read_notebook(temp_notebook)
        assert len(nb2.cells) == 1
        assert nb2.cells[0].source == "print('hello')"

    def test_kernel_tracker(self):
        """Test kernel tracking."""
        tracker = get_kernel_tracker()
        path = "/test/path.ipynb"
        kernel_id = "kernel-123"

        tracker.track(path, kernel_id)
        assert tracker.get(path) == kernel_id

        tracker.untrack(path)
        assert tracker.get(path) is None

    def test_get_tracked_kernel_none(self):
        """Test getting non-existent kernel."""
        tracker = get_kernel_tracker()
        result = tracker.get("/nonexistent/path.ipynb")
        assert result is None

    def test_convert_outputs_to_notebook_nodes(self):
        """Test converting outputs to notebook nodes."""
        outputs = [
            {"output_type": "stream", "name": "stdout", "text": "hello\n"},
            {"output_type": "execute_result", "data": {"text/plain": "42"}},
        ]
        nodes = convert_outputs_to_notebook_nodes(outputs)
        assert len(nodes) == 2
        assert nodes[0]["output_type"] == "stream"

    def test_output_filter(self):
        """Test output filtering."""
        # Small output - should not be filtered
        assert not OutputFilter.should_filter(["text/plain"], 100)

        # Large image - should be filtered
        assert OutputFilter.should_filter(["image/png"], 5000)

        # Test placeholder creation
        placeholder = OutputFilter.create_placeholder(["image/png", "image/jpeg"])
        assert "Filtered" in placeholder["data"]["text/plain"]
        assert "image/png" in placeholder["data"]["text/plain"]


class TestServerContext:
    """Test server context management."""

    def test_set_get_server_context(self, mock_serverapp):
        """Test setting and getting server context."""
        set_server_context(mock_serverapp)
        assert get_server_context() == mock_serverapp

    def test_get_server_context_none(self):
        """Test getting server context when not set."""
        assert get_server_context() is None


class TestListFunctions:
    """Test list functions."""

    @pytest.mark.asyncio
    async def test_list_notebook_kernels(self, mock_serverapp):
        """Test listing available kernel specs."""
        set_server_context(mock_serverapp)
        result = await list_notebook_kernels()
        assert "kernelspecs" in result
        assert "python3" in result["kernelspecs"]
        assert "ir" in result["kernelspecs"]


class TestExecuteCell:
    """Test execute_cell function."""

    @pytest.mark.asyncio
    async def test_execute_cell_by_position_no_context(self, temp_notebook):
        """Test execute without server context."""
        # Add a code cell first
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("print('test')"))
        write_notebook(temp_notebook, nb)

        # ExecutionEngine checks server context and returns error dict
        result = await execute_cell(temp_notebook, position_index=0)
        assert result["status"] == "error"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_cell_by_position(self, temp_notebook, mock_serverapp):
        """Test executing cell by position."""
        set_server_context(mock_serverapp)

        # Add a code cell
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("print('hello')"))
        write_notebook(temp_notebook, nb)

        result = await execute_cell(temp_notebook, position_index=0)

        assert result["status"] == "ok"
        assert "execution_count" in result

    @pytest.mark.asyncio
    async def test_execute_cell_reuses_session_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """execute_cell without setup_notebook reuses an existing session kernel."""
        set_server_context(mock_serverapp)

        # Simulate notebook already open (e.g., in JupyterLab) with a kernel
        existing_kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        normalized_path = normalize_path(temp_notebook)
        mock_serverapp.session_manager.add_session(
            normalized_path, existing_kernel_id, "python3"
        )

        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        write_notebook(temp_notebook, nb)

        result = await execute_cell(temp_notebook, position_index=0)

        assert result["status"] == "ok"
        assert result["kernel_id"] == existing_kernel_id
        assert result["started_new_kernel"] is False
        # No new kernel was forked
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_execute_cell_starts_session_backed_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """Auto-started kernels get a Jupyter session so they are discoverable later."""
        set_server_context(mock_serverapp)

        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        write_notebook(temp_notebook, nb)

        result = await execute_cell(temp_notebook, position_index=0)

        assert result["status"] == "ok"
        assert result["started_new_kernel"] is True
        # Kernel is session-backed, not a bare orphan kernel
        sessions = await mock_serverapp.session_manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["kernel"]["id"] == result["kernel_id"]
        # And tracked for subsequent calls
        tracker = get_kernel_tracker()
        assert tracker.get(normalize_path(temp_notebook)) == result["kernel_id"]

    @pytest.mark.asyncio
    async def test_reconnect_after_auto_start_reuses_same_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """Regression: setup_notebook after an auto-started kernel must reuse it.

        Scenario: agent executes cells without calling setup_notebook first
        (kernel auto-started), then later "reconnects" via setup_notebook.
        The same kernel must be reused so user state is preserved.
        """
        set_server_context(mock_serverapp)

        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        write_notebook(temp_notebook, nb)

        exec_result = await execute_cell(temp_notebook, position_index=0)
        auto_kernel_id = exec_result["kernel_id"]

        # Later: agent reconnects to the same notebook
        setup_result = await setup_notebook(temp_notebook, kernel_name="python3")

        assert setup_result["kernel_id"] == auto_kernel_id
        assert len(mock_serverapp.kernel_manager.kernels) == 1


class TestSetupNotebook:
    """Test setup_notebook function."""

    @pytest.mark.asyncio
    async def test_setup_notebook_create_new(self, tmp_path, mock_serverapp):
        """Test creating a new notebook."""
        set_server_context(mock_serverapp)
        nb_path = tmp_path / "new_notebook.ipynb"

        result = await setup_notebook(str(nb_path), kernel_name="python3")
        assert result["exists"] is False
        assert result["kernel_name"] == "python3"
        assert "kernel_id" in result
        assert Path(nb_path).exists()

    @pytest.mark.asyncio
    async def test_setup_notebook_existing(self, temp_notebook, mock_serverapp):
        """Test setup with existing notebook."""
        set_server_context(mock_serverapp)
        result = await setup_notebook(temp_notebook, kernel_name="python3")
        assert result["exists"] is True
        assert result["kernel_name"] == "python3"

    @pytest.mark.asyncio
    async def test_setup_notebook_r_kernel(self, tmp_path, mock_serverapp):
        """Test setup with R kernel."""
        set_server_context(mock_serverapp)
        nb_path = tmp_path / "r_notebook.ipynb"

        result = await setup_notebook(str(nb_path), kernel_name="ir")
        assert result["kernel_name"] == "ir"

    @pytest.mark.asyncio
    async def test_setup_notebook_reuses_existing_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """Test that setup_notebook reuses an existing kernel from a session."""
        set_server_context(mock_serverapp)

        # Simulate a notebook already open with a kernel
        # First, add a kernel to the mock kernel manager
        existing_kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")

        # Add a session for the notebook with this kernel
        normalized_path = normalize_path(temp_notebook)
        mock_serverapp.session_manager.add_session(
            normalized_path, existing_kernel_id, "python3"
        )

        # Now call setup_notebook - it should reuse the existing kernel
        result = await setup_notebook(temp_notebook, kernel_name="python3")

        # Verify the existing kernel was reused
        assert result["kernel_id"] == existing_kernel_id
        assert result["exists"] is True
        assert result["kernel_name"] == "python3"

        # Verify no new kernel was started (should still have only 1 kernel)
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_starts_new_when_no_session(
        self, temp_notebook, mock_serverapp
    ):
        """Test that setup_notebook starts a new kernel when no session exists."""
        set_server_context(mock_serverapp)

        # Call setup_notebook without any existing session
        result = await setup_notebook(temp_notebook, kernel_name="python3")

        # Verify a new kernel was started
        assert result["kernel_id"] is not None
        assert result["exists"] is True  # Notebook file exists
        assert result["kernel_name"] == "python3"

        # Verify a kernel was created
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_force_new_kernel(self, temp_notebook, mock_serverapp):
        """force_new_kernel REPLACES the notebook's kernel; it does not add a second.

        This assertion used to require 2 kernels afterwards, i.e. it encoded the leak:
        SessionManager.create_session does not dedupe by path, so each forced call left
        the previous session and kernel running. One notebook then had several kernels,
        this client and JupyterLab could attach to different ones, and the abandoned
        kernels held their memory until the server stopped.
        """
        set_server_context(mock_serverapp)

        # Simulate a notebook already open with a kernel
        existing_kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        normalized_path = normalize_path(temp_notebook)
        mock_serverapp.session_manager.add_session(
            normalized_path, existing_kernel_id, "python3"
        )

        # Call setup_notebook with force_new_kernel=True
        result = await setup_notebook(
            temp_notebook, kernel_name="python3", force_new_kernel=True
        )

        # Verify a NEW kernel was started (not the existing one)
        assert result["kernel_id"] != existing_kernel_id
        assert result["exists"] is True
        assert result["kernel_name"] == "python3"

        # The old kernel is gone, not left behind: exactly one kernel, one session.
        assert len(mock_serverapp.kernel_manager.kernels) == 1
        assert existing_kernel_id not in mock_serverapp.kernel_manager.kernels
        sessions = await mock_serverapp.session_manager.list_sessions()
        assert (
            len([s for s in sessions if normalize_path(s["path"]) == normalized_path])
            == 1
        )

    @pytest.mark.asyncio
    async def test_setup_notebook_handles_dead_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """Test that setup_notebook handles a dead kernel gracefully."""
        set_server_context(mock_serverapp)

        # Add a session with a kernel ID that doesn't exist in kernel_manager
        normalized_path = normalize_path(temp_notebook)
        dead_kernel_id = "kernel-999-dead"
        mock_serverapp.session_manager.add_session(
            normalized_path, dead_kernel_id, "python3"
        )

        # Call setup_notebook - should detect dead kernel and start new one
        result = await setup_notebook(temp_notebook, kernel_name="python3")

        # Verify a new kernel was started (not the dead one)
        assert result["kernel_id"] != dead_kernel_id
        assert result["kernel_id"] is not None
        assert result["exists"] is True
        assert result["kernel_name"] == "python3"

        # Verify a new kernel was created
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_reuses_tracked_kernel_without_session(
        self, temp_notebook, mock_serverapp
    ):
        """setup_notebook falls back to a tracked kernel when no session exists."""
        set_server_context(mock_serverapp)

        # A live kernel tracked for the notebook, but with no Jupyter session
        kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        tracker = get_kernel_tracker()
        tracker.track(normalize_path(temp_notebook), kernel_id)

        result = await setup_notebook(temp_notebook, kernel_name="python3")

        assert result["kernel_id"] == kernel_id
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_ignores_dead_tracked_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """A dead tracked kernel is untracked and a new kernel is started."""
        set_server_context(mock_serverapp)

        tracker = get_kernel_tracker()
        tracker.track(normalize_path(temp_notebook), "kernel-dead-123")

        result = await setup_notebook(temp_notebook, kernel_name="python3")

        assert result["kernel_id"] != "kernel-dead-123"
        assert result["kernel_id"] is not None
        assert len(mock_serverapp.kernel_manager.kernels) == 1


class TestSwitchNotebookKernel:
    """Test switch_notebook_kernel function."""

    @pytest.mark.asyncio
    async def test_switch_notebook_kernel(self, temp_notebook, mock_serverapp):
        """Test switching notebook kernel."""
        set_server_context(mock_serverapp)

        # First setup with python
        await setup_notebook(temp_notebook, "python3")

        # Then switch to R
        result = await switch_notebook_kernel(temp_notebook, "ir")
        assert result["kernel_name"] == "ir"
        assert "kernel_id" in result

        # Verify notebook metadata was updated
        nb = read_notebook(temp_notebook)
        assert nb.metadata["kernelspec"]["name"] == "ir"


class TestShutdownNotebook:
    """Test shutdown_notebook function."""

    @pytest.mark.asyncio
    async def test_shutdown_notebook_no_kernel(self, temp_notebook):
        """Test shutdown notebook with no tracked kernel."""
        result = await shutdown_notebook(temp_notebook)
        assert "message" in result
        # kernel_id is excluded when None due to exclude_none=True
        assert result.get("kernel_id") is None

    @pytest.mark.asyncio
    async def test_shutdown_notebook_with_kernel(self, temp_notebook, mock_serverapp):
        """Test shutdown notebook with tracked kernel."""
        set_server_context(mock_serverapp)

        # Setup notebook (starts kernel)
        setup_result = await setup_notebook(temp_notebook)
        kernel_id = setup_result["kernel_id"]

        # Shutdown
        result = await shutdown_notebook(temp_notebook, shutdown_kernel=True)
        assert result["kernel_id"] == kernel_id
        assert "Notebook session closed" in result["message"]


class TestCellTools:
    """Test the five fine-grained cell tools."""

    @pytest.mark.asyncio
    async def test_add_code_cell_no_execute(self, temp_notebook):
        """Test adding a code cell without execution."""
        result = await add_code_cell(temp_notebook, "print('hello')", execute=False)
        assert "message" in result
        assert "position" in result

        # Verify cell was added
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 1
        assert nb.cells[0].source == "print('hello')"

    @pytest.mark.asyncio
    async def test_add_code_cell_with_execute(self, temp_notebook, mock_serverapp):
        """Test adding and executing a code cell."""
        set_server_context(mock_serverapp)

        result = await add_code_cell(temp_notebook, "print('hello')", execute=True)
        assert result["status"] == "ok"
        assert "execution_count" in result

    @pytest.mark.asyncio
    async def test_add_code_cell_reuses_session_kernel(
        self, temp_notebook, mock_serverapp
    ):
        """add_code_cell reuses an existing session kernel instead of forking one."""
        set_server_context(mock_serverapp)

        existing_kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        mock_serverapp.session_manager.add_session(
            normalize_path(temp_notebook), existing_kernel_id, "python3"
        )

        result = await add_code_cell(temp_notebook, "x = 1", execute=True)

        assert result["status"] == "ok"
        assert result["kernel_id"] == existing_kernel_id
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_add_markdown_cell(self, temp_notebook):
        """Test adding a markdown cell."""
        result = await add_markdown_cell(temp_notebook, "# Title")
        assert "message" in result

        # Verify cell was added
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 1
        assert nb.cells[0].cell_type == "markdown"
        assert nb.cells[0].source == "# Title"

    @pytest.mark.asyncio
    async def test_edit_code_cell(self, temp_notebook, mock_serverapp):
        """Test editing a code cell."""
        set_server_context(mock_serverapp)

        # First add a cell
        await add_code_cell(temp_notebook, "x = 1", execute=False)

        # Then edit it
        result = await edit_code_cell(temp_notebook, 0, "x = 2", execute=True)
        assert result["status"] == "ok"

        # Verify edit
        nb = read_notebook(temp_notebook)
        assert nb.cells[0].source == "x = 2"

    @pytest.mark.asyncio
    async def test_edit_markdown_cell(self, temp_notebook):
        """Test editing a markdown cell."""
        # First add a cell
        await add_markdown_cell(temp_notebook, "# Old")

        # Then edit it
        result = await edit_markdown_cell(temp_notebook, 0, "# New")
        assert "message" in result

        # Verify edit
        nb = read_notebook(temp_notebook)
        assert nb.cells[0].source == "# New"

    @pytest.mark.asyncio
    async def test_delete_cell(self, temp_notebook):
        """Test deleting a cell."""
        # First add a cell
        await add_code_cell(temp_notebook, "x = 1", execute=False)

        # Then delete it
        result = await delete_cell(temp_notebook, 0)
        assert "message" in result

        # Verify cell was deleted
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 0

    @pytest.mark.asyncio
    async def test_add_code_at_position(self, temp_notebook):
        """Test adding code cell at specific position."""
        # Add two cells
        await add_code_cell(temp_notebook, "x = 1", execute=False)
        await add_code_cell(temp_notebook, "x = 3", execute=False)

        # Insert at position 1 (middle)
        await add_code_cell(temp_notebook, "x = 2", position_index=1, execute=False)

        # Verify order
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 3
        assert nb.cells[0].source == "x = 1"
        assert nb.cells[1].source == "x = 2"
        assert nb.cells[2].source == "x = 3"


class TestViewNotebook:
    """Test view_notebook function."""

    @pytest.mark.asyncio
    async def test_view_notebook_all_cells(self, temp_notebook):
        """Test viewing all cells in order."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        nb.cells.append(new_markdown_cell("# Header"))
        write_notebook(temp_notebook, nb)

        result = await view_notebook(temp_notebook)
        assert isinstance(result, list)
        assert len(result) == 2
        # List index == position_index (documented contract)
        assert result[0]["cell_type"] == "code"
        assert result[0]["source"] == "x = 1"
        assert result[1]["cell_type"] == "markdown"
        assert result[1]["source"] == "# Header"

    @pytest.mark.asyncio
    async def test_view_notebook_empty(self, temp_notebook):
        """Test viewing an empty notebook returns an empty list."""
        result = await view_notebook(temp_notebook)
        assert result == []


class TestReadCell:
    """Test read_cell convenience function."""

    @pytest.mark.asyncio
    async def test_read_cell_by_position(self, temp_notebook):
        """Test reading cell by position."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("print('test')"))
        write_notebook(temp_notebook, nb)

        result = await read_cell(temp_notebook, position_index=0)
        assert result["source"] == "print('test')"
        assert result["position"] == 0

    @pytest.mark.asyncio
    async def test_read_cell_by_execution_count(self, temp_notebook):
        """Test reading cell by execution count."""
        nb = read_notebook(temp_notebook)
        cell = new_code_cell("x = 1")
        cell.execution_count = 42
        nb.cells.append(cell)
        write_notebook(temp_notebook, nb)

        result = await read_cell(temp_notebook, execution_count=42)
        assert result["source"] == "x = 1"
        assert result["execution_count"] == 42

    @pytest.mark.asyncio
    async def test_read_cell_no_identifier(self, temp_notebook):
        """Test that read_cell requires an identifier."""
        with pytest.raises(ValueError, match="exactly ONE"):
            await read_cell(temp_notebook)

    @pytest.mark.asyncio
    async def test_read_cell_multiple_identifiers(self, temp_notebook):
        """Test that read_cell rejects multiple identifiers."""
        with pytest.raises(ValueError, match="exactly ONE"):
            await read_cell(temp_notebook, position_index=0, execution_count=1)


@pytest.mark.asyncio
async def test_output_filtering():
    """Test that OutputFilter properly filters large outputs."""
    # Create mock outputs with large image data
    outputs = [
        {"output_type": "stream", "name": "stdout", "text": "Hello World\n"},
        {
            "output_type": "display_data",
            "data": {
                "text/plain": "Plot",
                "image/png": "iVBORw0KGgoAAAANS..." * 1000,  # Large base64 data
            },
        },
        {
            "output_type": "execute_result",
            "data": {"text/html": "<div>Large HTML content...</div>" * 100},
            "execution_count": 1,
        },
    ]

    # Filter the outputs
    filtered = OutputFilter.filter_outputs(outputs)

    # Verify filtering
    assert len(filtered) == 3

    # Stream output should be unchanged
    assert filtered[0]["output_type"] == "stream"
    assert filtered[0]["text"] == "Hello World\n"

    # Image data should be filtered (large size)
    # After filtering, the output type becomes execute_result with placeholder
    if "Filtered" in str(filtered[1]["data"].get("text/plain", "")):
        # Was filtered - check it has placeholder
        assert "image/png" in filtered[1]["data"]["text/plain"]
    else:
        # Wasn't filtered (original output preserved)
        assert filtered[1]["output_type"] == "display_data"

    # HTML might be filtered depending on size
    # Check the last output has execution_count
    has_execution_count = any("execution_count" in out for out in filtered)
    assert has_execution_count


class TestIntegration:
    """Integration tests for complete workflows."""

    @pytest.mark.asyncio
    async def test_complete_notebook_workflow(self, tmp_path, mock_serverapp):
        """Test a complete notebook workflow."""
        set_server_context(mock_serverapp)
        nb_path = tmp_path / "workflow.ipynb"

        # 1. Setup notebook
        setup_result = await setup_notebook(str(nb_path))
        assert setup_result["exists"] is False
        assert "kernel_id" in setup_result

        # 2. Add markdown header
        await add_markdown_cell(str(nb_path), "# My Notebook")

        # 3. Add and execute code
        exec_result = await add_code_cell(str(nb_path), "x = 42", execute=True)
        assert exec_result["status"] == "ok"

        # 4. Query notebook
        cells = await view_notebook(str(nb_path))
        assert len(cells) == 2
        assert cells[0]["cell_type"] == "markdown"
        assert cells[1]["cell_type"] == "code"

        # 5. Read specific cell
        cell = await read_cell(str(nb_path), position_index=1)
        assert cell["source"] == "x = 42"

        # 6. Shutdown
        await shutdown_notebook(str(nb_path))


class TestExecutionMessageHandling:
    """Regression tests for kernel message collection (execution_utils)."""

    @pytest.mark.asyncio
    async def test_iopub_gap_does_not_crash_execution(self):
        """A queue.Empty from an idle iopub poll must not abort execution.

        jupyter_client raises queue.Empty (not asyncio.TimeoutError) when the
        channel poll times out; this used to propagate and lose the outputs.
        """

        class GapClient:
            def __init__(self):
                self.iopub_calls = 0

            def execute(self, _code, **_kwargs):
                return "msg-1"

            async def get_shell_msg(self, timeout=None):
                return {
                    "header": {"msg_type": "execute_reply"},
                    "parent_header": {"msg_id": "msg-1"},
                    "content": {"status": "ok", "execution_count": 7},
                }

            async def get_iopub_msg(self, timeout=None):
                self.iopub_calls += 1
                if self.iopub_calls == 1:
                    raise Empty
                return {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-1"},
                    "content": {"execution_state": "idle"},
                }

        result = await execute_code_on_kernel(GapClient(), "x = 1", "k1", timeout=5)
        assert result["status"] == "ok"
        assert result["execution_count"] == 7

    @pytest.mark.asyncio
    async def test_stale_shell_message_is_drained(self):
        """A leftover shell reply from a previous execution must be skipped."""

        class StaleShellClient:
            def __init__(self):
                self.shell_msgs = [
                    {  # stale reply from an earlier, timed-out execution
                        "header": {"msg_type": "execute_reply"},
                        "parent_header": {"msg_id": "old-msg"},
                        "content": {"status": "ok", "execution_count": 3},
                    },
                    {  # the reply we are waiting for
                        "header": {"msg_type": "execute_reply"},
                        "parent_header": {"msg_id": "msg-2"},
                        "content": {"status": "ok", "execution_count": 4},
                    },
                ]

            def execute(self, _code, **_kwargs):
                return "msg-2"

            async def get_shell_msg(self, timeout=None):
                return self.shell_msgs.pop(0)

            async def get_iopub_msg(self, timeout=None):
                return {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-2"},
                    "content": {"execution_state": "idle"},
                }

        result = await execute_code_on_kernel(
            StaleShellClient(), "x = 1", "k1", timeout=5
        )
        assert result["execution_count"] == 4


class HangingKernelClient:
    """Fake client whose cell never finishes on its own.

    An interrupt makes it behave like a real kernel: an error output
    (KeyboardInterrupt) then idle. With stops_on_interrupt=False it ignores
    the interrupt (e.g. stuck in C code).
    """

    def __init__(self, stops_on_interrupt: bool = True):
        self.stops_on_interrupt = stops_on_interrupt
        self.interrupt_calls = 0
        self.iopub_calls = 0
        self.iopub_active = 0
        self._pending: list[dict] = [
            {
                "header": {"msg_type": "stream"},
                "parent_header": {"msg_id": "msg-hang"},
                "content": {"name": "stdout", "text": "started\n"},
            }
        ]

    def execute(self, _code, **_kwargs):
        return "msg-hang"

    async def interrupt(self):
        self.interrupt_calls += 1
        if self.stops_on_interrupt:
            self._pending += [
                {
                    "header": {"msg_type": "error"},
                    "parent_header": {"msg_id": "msg-hang"},
                    "content": {
                        "ename": "KeyboardInterrupt",
                        "evalue": "",
                        "traceback": ["KeyboardInterrupt"],
                    },
                },
                {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-hang"},
                    "content": {"execution_state": "idle"},
                },
            ]

    async def get_shell_msg(self, timeout=None):
        await asyncio.sleep(timeout or 0)
        raise Empty

    async def get_iopub_msg(self, timeout=None):
        self.iopub_calls += 1
        self.iopub_active += 1
        try:
            if self._pending:
                return self._pending.pop(0)
            await asyncio.sleep(min(timeout or 0, 0.02))
            raise Empty
        finally:
            self.iopub_active -= 1


class TestTimeoutInterruptAndCancel:
    """Timeouts and cancellation stop the kernel instead of leaving it busy."""

    @pytest.mark.asyncio
    async def test_timeout_interrupts_kernel_and_drains_to_idle(self):
        client = HangingKernelClient()

        result = await execute_code_on_kernel(
            client,
            "while True: pass",
            "k1",
            timeout=0.2,
            interrupt=client.interrupt,
            interrupt_grace=2,
        )

        assert client.interrupt_calls == 1
        assert result["status"] == "error"
        assert "timed out after 0.2s; the kernel was interrupted" in result["error"]
        output_types = [o["output_type"] for o in result["outputs"]]
        assert output_types == ["stream", "error"]

    @pytest.mark.asyncio
    async def test_timeout_interrupts_even_if_shell_poll_returns_early(self):
        """jupyter_client may raise Empty a few ms before the requested timeout.

        The timeout must still be detected (and the kernel interrupted)
        instead of falling through to the slower "timed out while collecting
        outputs" path with the cell left running.
        """

        class EarlyEmptyClient(HangingKernelClient):
            async def get_shell_msg(self, timeout=None):
                await asyncio.sleep((timeout or 0) * 0.9)  # gives up early
                raise Empty

        client = EarlyEmptyClient()

        result = await execute_code_on_kernel(
            client,
            "while True: pass",
            "k1",
            timeout=0.3,
            interrupt=client.interrupt,
            interrupt_grace=2,
        )

        assert client.interrupt_calls == 1
        assert "the kernel was interrupted" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_shell_reply_does_not_block_after_idle(self, monkeypatch):
        """If iopub says the kernel is idle, don't wait the full timeout.

        A lost execute_reply must not hold the call (and the kernel lock)
        until the deadline, which can be hours away.
        """
        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_utils.SHELL_REPLY_GRACE_SECS", 0.2
        )

        class NoReplyClient:
            def execute(self, _code, **_kwargs):
                return "msg-nr"

            async def get_shell_msg(self, timeout=None):
                await asyncio.sleep(timeout or 0)  # the reply never comes
                raise Empty

            async def get_iopub_msg(self, timeout=None):
                return {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-nr"},
                    "content": {"execution_state": "idle"},
                }

        start = asyncio.get_running_loop().time()
        result = await execute_code_on_kernel(
            NoReplyClient(), "x = 1", "k1", timeout=3600, interrupt=None
        )

        assert asyncio.get_running_loop().time() - start < 2
        assert result["status"] == "ok"
        assert result["execution_count"] is None

    @pytest.mark.asyncio
    async def test_interrupt_that_does_not_stop_kernel_reports_still_busy(self):
        client = HangingKernelClient(stops_on_interrupt=False)

        result = await execute_code_on_kernel(
            client,
            "stuck_in_c()",
            "k1",
            timeout=0.2,
            interrupt=client.interrupt,
            interrupt_grace=0.3,
        )

        assert client.interrupt_calls == 1
        assert result["status"] == "error"
        assert "still busy" in result["error"]
        assert "restart_notebook_kernel" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_without_interrupt_keeps_previous_behavior(self, monkeypatch):
        # Shorten the existing post-deadline drain window to keep this fast.
        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_utils.IOPUB_DRAIN_GRACE_SECS", 0.2
        )
        client = HangingKernelClient()

        result = await execute_code_on_kernel(client, "x", "k1", timeout=0.2)

        assert client.interrupt_calls == 0
        assert result["status"] == "error"
        assert "timed out" in result["error"]

    @staticmethod
    def _start(client, cleanup, **kwargs):
        return asyncio.create_task(
            execute_code_on_kernel(
                client,
                "x",
                "k1",
                timeout=60,
                interrupt=client.interrupt,
                cancel_cleanup=cleanup,
                **kwargs,
            )
        )

    @pytest.mark.asyncio
    async def test_cancel_interrupts_kernel_and_stops_collector(self):
        client = HangingKernelClient()
        cleanup = []
        task = self._start(client, cleanup)
        await asyncio.sleep(0.1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(cleanup) == 1
        await asyncio.wait_for(asyncio.gather(*cleanup), timeout=2)

        assert client.interrupt_calls == 1
        # No orphaned iopub collector keeps reading the shared client.
        calls = client.iopub_calls
        await asyncio.sleep(0.1)
        assert client.iopub_active == 0
        assert client.iopub_calls == calls

    @pytest.mark.asyncio
    async def test_cancel_drains_kernel_to_idle_before_returning(self):
        """The kernel lock must not be released while the cell still runs.

        Otherwise the next execution reaches the kernel before the interrupted
        cell fails, and ipykernel aborts it as "queued behind an error".
        """
        client = HangingKernelClient()
        cleanup = []
        task = self._start(client, cleanup)
        await asyncio.sleep(0.1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.gather(*cleanup), timeout=2)

        # The KeyboardInterrupt error and the idle status were consumed.
        assert client._pending == []

    @pytest.mark.asyncio
    async def test_repeated_cancellation_still_drains_in_background(self):
        """anyio re-delivers cancellation at every await; cleanup must survive."""
        client = HangingKernelClient()
        cleanup = []
        task = self._start(client, cleanup)
        await asyncio.sleep(0.1)

        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.gather(*cleanup), timeout=2)

        assert client.interrupt_calls == 1
        assert client._pending == []
        assert client.iopub_active == 0

    @pytest.mark.asyncio
    async def test_cancel_drain_is_bounded_by_grace(self):
        client = HangingKernelClient(stops_on_interrupt=False)
        cleanup = []
        task = self._start(client, cleanup, interrupt_grace=0.3)
        await asyncio.sleep(0.1)

        start = asyncio.get_running_loop().time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.gather(*cleanup), timeout=2)

        assert asyncio.get_running_loop().time() - start < 2
        assert client.iopub_active == 0

    @pytest.mark.asyncio
    async def test_engine_keeps_kernel_reserved_until_cleanup_finishes(
        self, mock_serverapp, monkeypatch
    ):
        """The next execution must start only after the cancelled one ended."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()
        events = []

        async def fake_execute(_client, code, *_args, cancel_cleanup=None, **_kw):
            if code == "slow":
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:

                    async def cleanup():
                        await asyncio.sleep(0.3)
                        events.append("cleanup done")

                    cancel_cleanup.append(asyncio.create_task(cleanup()))
                    raise
            events.append(f"run {code}")
            return {"execution_count": 1, "outputs": [], "status": "ok"}

        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_engine.execute_code_on_kernel",
            fake_execute,
        )
        engine = container.execution_engine
        slow = asyncio.create_task(
            engine.execute_cell_code("slow", kernel_id="kernel-live")
        )
        await asyncio.sleep(0.1)
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow

        result = await engine.execute_cell_code("next", kernel_id="kernel-live")

        assert result.status == "ok"
        assert events == ["cleanup done", "run next"]

    @pytest.mark.asyncio
    async def test_aborted_execution_is_reported_as_error(self):
        """ipykernel aborts cells queued behind a failing one; say so."""

        class AbortedClient:
            def execute(self, _code, **_kwargs):
                return "msg-ab"

            async def get_shell_msg(self, timeout=None):
                return {
                    "header": {"msg_type": "execute_reply"},
                    "parent_header": {"msg_id": "msg-ab"},
                    "content": {"status": "aborted", "execution_count": None},
                }

            async def get_iopub_msg(self, timeout=None):
                return {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-ab"},
                    "content": {"execution_state": "idle"},
                }

        result = await execute_code_on_kernel(AbortedClient(), "1 + 1", "k1", timeout=5)

        assert result["status"] == "error"
        assert "aborted" in result["error"]
        assert result["outputs"] == []

    @pytest.mark.asyncio
    async def test_failing_interrupt_is_logged_not_raised(self):
        client = HangingKernelClient()

        async def broken_interrupt():
            msg = "kernel gone"
            raise RuntimeError(msg)

        result = await execute_code_on_kernel(
            client,
            "x",
            "k1",
            timeout=0.2,
            interrupt=broken_interrupt,
            interrupt_grace=0.3,
        )

        assert result["status"] == "error"
        assert "still busy" in result["error"]


class TestSessionMatching:
    """Regression tests for session-to-notebook matching."""

    @pytest.mark.asyncio
    async def test_same_filename_different_dir_not_matched(
        self, tmp_path, mock_serverapp
    ):
        """Notebooks sharing a filename in different folders must not share kernels."""
        set_server_context(mock_serverapp)

        dir_a = tmp_path / "project_a"
        dir_b = tmp_path / "project_b"
        dir_a.mkdir()
        dir_b.mkdir()
        nb_a = dir_a / "analysis.ipynb"
        nb_b = dir_b / "analysis.ipynb"
        for p in (nb_a, nb_b):
            with p.open("w") as f:
                nbformat.write(new_notebook(cells=[]), f)

        # Notebook A has a running session
        kernel_a = await mock_serverapp.kernel_manager.start_kernel("python3")
        mock_serverapp.session_manager.add_session(
            normalize_path(str(nb_a)), kernel_a, "python3"
        )

        # Setting up notebook B must NOT reuse A's kernel
        result = await setup_notebook(str(nb_b), kernel_name="python3")

        assert result["kernel_id"] != kernel_a
        assert len(mock_serverapp.kernel_manager.kernels) == 2

    @pytest.mark.asyncio
    async def test_non_notebook_session_does_not_abort_scan(
        self, temp_notebook, mock_serverapp
    ):
        """A console session with a non-.ipynb path must not break matching."""
        set_server_context(mock_serverapp)

        # Console-style session whose path normalize_path would reject
        mock_serverapp.session_manager.add_session(
            "console-1", "kernel-console", "python3"
        )

        # Valid session for our notebook, listed after the console session
        kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        mock_serverapp.session_manager.add_session(
            normalize_path(temp_notebook), kernel_id, "python3"
        )

        result = await setup_notebook(temp_notebook, kernel_name="python3")
        assert result["kernel_id"] == kernel_id

    @pytest.mark.asyncio
    async def test_setup_notebook_unknown_kernel_raises(
        self, temp_notebook, mock_serverapp
    ):
        """An unresolvable kernel name fails loudly, listing available kernels."""
        set_server_context(mock_serverapp)

        with pytest.raises(ValueError, match="python3"):
            await setup_notebook(temp_notebook, kernel_name="nonexistent_kernel")


class TestReadOnlyAccess:
    """Read operations must never rewrite the notebook file."""

    @pytest.mark.asyncio
    async def test_view_and_read_do_not_rewrite_file(self, temp_notebook):
        """view_notebook/read_cell leave the file untouched (mtime stable)."""
        await add_code_cell(temp_notebook, "x = 1", execute=False)

        before = Path(temp_notebook).stat().st_mtime_ns
        await view_notebook(temp_notebook)
        await read_cell(temp_notebook, position_index=0)
        after = Path(temp_notebook).stat().st_mtime_ns

        assert before == after


class TestEditClearsOutputs:
    """Editing a code cell must not leave stale outputs from the old source."""

    @pytest.mark.asyncio
    async def test_edit_without_execute_clears_outputs(
        self, temp_notebook, mock_serverapp
    ):
        set_server_context(mock_serverapp)

        # Add + execute so the cell has outputs and an execution_count
        await add_code_cell(temp_notebook, "print('hello')", execute=True)

        # Edit without executing
        await edit_code_cell(temp_notebook, 0, "y = 2", execute=False)

        nb = read_notebook(temp_notebook)
        assert nb.cells[0].source == "y = 2"
        assert nb.cells[0].outputs == []
        assert nb.cells[0].execution_count is None


class TestOutputPlacement:
    """Outputs must land in the executed cell even if positions shift."""

    @pytest.mark.asyncio
    async def test_outputs_follow_cell_when_positions_shift(self, mock_serverapp):
        """The engine re-locates the cell by ID before writing outputs."""
        set_server_context(mock_serverapp)
        kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")

        class ShiftingAdapter:
            """Adapter stub where the cell moved from position 1 to 2."""

            notebook_path = "/tmp/shifting.ipynb"
            is_collaborative = False

            def __init__(self):
                self.updated_position = None

            async def find_cell_by_id(self, cell_id):
                return (None, 2)  # cell is now at position 2

            async def update_cell_outputs(self, position, _count, _outputs):
                self.updated_position = position

        adapter = ShiftingAdapter()
        await container.execution_engine.execute_and_update_cell(
            adapter, 1, "x = 1", kernel_id=kernel_id, cell_id="cell-abc"
        )
        assert adapter.updated_position == 2

    @pytest.mark.asyncio
    async def test_no_output_update_when_cell_deleted(self, mock_serverapp):
        """If the cell was deleted mid-execution, outputs are not misplaced."""
        set_server_context(mock_serverapp)
        kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")

        class DeletedAdapter:
            notebook_path = "/tmp/deleted.ipynb"
            is_collaborative = False

            def __init__(self):
                self.update_called = False

            async def find_cell_by_id(self, cell_id):
                return None  # cell no longer exists

            async def update_cell_outputs(self, *_args):
                self.update_called = True

        adapter = DeletedAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "x = 1", kernel_id=kernel_id, cell_id="cell-gone"
        )
        assert result.status == "ok"  # execution result still returned
        assert adapter.update_called is False


class OutputtingClient:
    """Mock kernel client that emits one stream output before going idle."""

    def __init__(self):
        self.channels_started = False
        self._shell = []
        self._iopub = []

    def start_channels(self):
        self.channels_started = True

    def stop_channels(self):
        self.channels_started = False

    async def wait_for_ready(self, timeout=10):
        pass

    def execute(self, _code, **_kwargs):
        msg_id = "msg-live-1"
        self._shell.append(
            {
                "header": {"msg_type": "execute_reply"},
                "parent_header": {"msg_id": msg_id},
                "content": {"status": "ok", "execution_count": 1},
            }
        )
        self._iopub = [
            {
                "header": {"msg_type": "stream"},
                "parent_header": {"msg_id": msg_id},
                "content": {"name": "stdout", "text": "tick\n"},
            },
            {
                "header": {"msg_type": "status"},
                "parent_header": {"msg_id": msg_id},
                "content": {"execution_state": "idle"},
            },
        ]
        return msg_id

    async def get_shell_msg(self, timeout=None):
        await asyncio.sleep(0.01)
        if self._shell:
            return self._shell.pop(0)
        raise asyncio.TimeoutError

    async def get_iopub_msg(self, timeout=None):
        await asyncio.sleep(0.01)
        if self._iopub:
            return self._iopub.pop(0)
        raise Empty


class OutputtingKernel:
    """Mock kernel handle returning an OutputtingClient."""

    def __init__(self):
        self.session = Session()
        self._client = OutputtingClient()

    def client(self, **_kwargs):
        return self._client


class RecordingCollabAdapter:
    """Collaborative adapter stub that records streaming calls."""

    notebook_path = "/tmp/collab.ipynb"
    is_collaborative = True

    def __init__(self):
        self.cleared = []
        self.appended = []
        self.updated = []

    async def find_cell_by_id(self, cell_id):
        return (None, 0)

    async def clear_cell_outputs(self, position):
        self.cleared.append(position)

    async def append_cell_output(self, position, output):
        self.appended.append((position, output))

    async def update_cell_outputs(self, position, execution_count, outputs):
        self.updated.append((position, execution_count, outputs))


class TestLiveOutputStreaming:
    """Outputs stream into the notebook while the cell is still executing."""

    @pytest.mark.asyncio
    async def test_outputs_stream_before_execution_finishes(self):
        """on_output fires while the kernel is still busy.

        The shell reply is withheld until the callback has seen the first
        output — this only completes if iopub is consumed concurrently with
        the shell wait (sequential collection would time out and lose the
        execution_count).
        """
        streamed = asyncio.Event()

        class LiveClient:
            def execute(self, _code, **_kwargs):
                return "msg-live"

            async def get_shell_msg(self, timeout=None):
                await asyncio.wait_for(streamed.wait(), timeout=3)
                return {
                    "header": {"msg_type": "execute_reply"},
                    "parent_header": {"msg_id": "msg-live"},
                    "content": {"status": "ok", "execution_count": 9},
                }

            async def get_iopub_msg(self, timeout=None):
                if not streamed.is_set():
                    return {
                        "header": {"msg_type": "stream"},
                        "parent_header": {"msg_id": "msg-live"},
                        "content": {"name": "stdout", "text": "early\n"},
                    }
                return {
                    "header": {"msg_type": "status"},
                    "parent_header": {"msg_id": "msg-live"},
                    "content": {"execution_state": "idle"},
                }

        async def on_output(_output):
            streamed.set()

        result = await execute_code_on_kernel(
            LiveClient(), "x", "k1", timeout=5, on_output=on_output
        )
        assert result["status"] == "ok"
        assert result["execution_count"] == 9
        assert result["outputs"] == [
            {"output_type": "stream", "name": "stdout", "text": "early\n"}
        ]

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_abort_execution(self):
        """A crashing callback disables streaming but execution completes."""
        calls = []

        async def bad_callback(output):
            calls.append(output)
            msg = "boom"
            raise RuntimeError(msg)

        result = await execute_code_on_kernel(
            OutputtingClient(), "x", "k1", timeout=5, on_output=bad_callback
        )
        assert result["status"] == "ok"
        assert len(result["outputs"]) == 1
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_collaborative_adapter_streams_and_skips_final_rewrite(
        self, mock_serverapp
    ):
        """Open notebook: clear at start, append per output, no final rewrite."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        adapter = RecordingCollabAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-1"
        )

        assert result.status == "ok"
        assert adapter.cleared == [0]
        assert [out["output_type"] for _, out in adapter.appended] == ["stream"]
        # Final write records execution_count only; streamed outputs are kept
        assert adapter.updated == [(0, 1, None)]

    @pytest.mark.asyncio
    async def test_append_failure_falls_back_to_batch_write(self, mock_serverapp):
        """If streaming breaks mid-run, the final batch write reconciles."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        class FailingAppendAdapter(RecordingCollabAdapter):
            async def append_cell_output(self, position, output):
                msg = "append rejected"
                raise RuntimeError(msg)

        adapter = FailingAppendAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-1"
        )

        assert result.status == "ok"
        assert len(adapter.updated) == 1
        position, execution_count, outputs = adapter.updated[0]
        assert (position, execution_count) == (0, 1)
        assert outputs == [
            {"output_type": "stream", "name": "stdout", "text": "tick\n"}
        ]

    @pytest.mark.asyncio
    async def test_cell_deleted_before_execution_skips_streaming(self, mock_serverapp):
        """Deleted cell: streaming disabled, no output update, result intact."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        class GoneAdapter(RecordingCollabAdapter):
            async def find_cell_by_id(self, cell_id):
                return None

        adapter = GoneAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-gone"
        )

        assert result.status == "ok"
        assert adapter.appended == []
        assert adapter.updated == []

    @pytest.mark.asyncio
    async def test_outputs_reach_streamer_and_progress_reporter(
        self, mock_serverapp, monkeypatch
    ):
        """Each output feeds both the live notebook and MCP progress."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()
        sent = []
        labels = []

        async def record(progress, total, message):
            sent.append((progress, total, message))

        def from_request(label, **_kwargs):
            labels.append(label)
            # min_interval=0: notify on every output so the test sees it.
            return ExecutionProgress(
                record,
                label,
                config=ProgressConfig(
                    heartbeat_interval=3600, min_interval=0, message_max_chars=120
                ),
            )

        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_engine.ExecutionProgress.from_request",
            from_request,
        )

        adapter = RecordingCollabAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-1"
        )

        assert result.status == "ok"
        assert [out["output_type"] for _, out in adapter.appended] == ["stream"]
        assert labels == ["collab.ipynb cell 0"]
        assert len(sent) == 1
        assert sent[0][2].startswith("collab.ipynb cell 0 · ")
        assert sent[0][2].endswith("· 1 output · tick")

    @pytest.mark.asyncio
    async def test_execution_without_request_context_has_no_reporter(
        self, mock_serverapp
    ):
        """Direct calls (no MCP request) execute exactly as before."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        assert ExecutionProgress.from_request("anything") is None
        result = await container.execution_engine.execute_cell_code(
            "print('tick')", kernel_id="kernel-live"
        )

        assert result.status == "ok"
        assert len(result.outputs) == 1

    @pytest.mark.asyncio
    async def test_noncollaborative_adapter_does_not_stream(self, mock_serverapp):
        """Closed notebook: no streaming calls, single batch write as before."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        class ClosedAdapter(RecordingCollabAdapter):
            is_collaborative = False

        adapter = ClosedAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-1"
        )

        assert result.status == "ok"
        assert adapter.cleared == []
        assert adapter.appended == []
        assert len(adapter.updated) == 1
        assert adapter.updated[0][2] is not None  # full batch write

    @pytest.mark.asyncio
    async def test_room_is_kept_alive_while_a_silent_cell_runs(
        self, mock_serverapp, monkeypatch
    ):
        """A cell without output makes no adapter calls, yet the browser may
        have left: the room must be kept alive for the whole execution."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()
        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_engine.EXECUTION_CONFIG",
            dataclasses.replace(EXECUTION_CONFIG, room_keepalive_interval=0.01),
        )

        async def silent_execute(*_args, **_kwargs):
            await asyncio.sleep(0.2)
            return {"execution_count": 1, "outputs": [], "status": "ok"}

        monkeypatch.setattr(
            "jupyter_mcp_executor.execution_engine.execute_code_on_kernel",
            silent_execute,
        )

        class KeptAliveAdapter(RecordingCollabAdapter):
            kept_alive = 0

            def keep_alive(self):
                self.kept_alive += 1

        adapter = KeptAliveAdapter()
        await container.execution_engine.execute_and_update_cell(
            adapter,
            0,
            "import time; time.sleep(600)",
            kernel_id="kernel-live",
            cell_id="cell-1",
        )
        kept_alive_during_run = adapter.kept_alive
        await asyncio.sleep(0.05)

        assert kept_alive_during_run >= 3
        assert adapter.kept_alive == kept_alive_during_run  # stopped afterwards

    @pytest.mark.asyncio
    async def test_replaced_room_gets_the_full_outputs(self, mock_serverapp):
        """The room was recreated mid-run: what was streamed into the old one
        may be missing from the new one, so all outputs are written again."""
        set_server_context(mock_serverapp)
        mock_serverapp.kernel_manager.kernels["kernel-live"] = OutputtingKernel()

        class ReplacedRoomAdapter(RecordingCollabAdapter):
            generation = 0

            async def append_cell_output(self, position, output):
                self.generation += 1  # the adapter followed a recreated room
                await super().append_cell_output(position, output)

        adapter = ReplacedRoomAdapter()
        result = await container.execution_engine.execute_and_update_cell(
            adapter, 0, "print('tick')", kernel_id="kernel-live", cell_id="cell-1"
        )

        assert adapter.appended  # streaming itself still worked
        assert adapter.updated[-1][2] == result.outputs

    @pytest.mark.asyncio
    async def test_nbformat_append_cell_output_persists(self, temp_notebook):
        """NbformatAdapter.append_cell_output appends to the file on disk."""
        await add_code_cell(temp_notebook, "x = 1", execute=False)

        adapter = NbformatAdapter(normalize_path(temp_notebook))
        await adapter.append_cell_output(
            0, {"output_type": "stream", "name": "stdout", "text": "a\n"}
        )
        await adapter.append_cell_output(
            0, {"output_type": "stream", "name": "stdout", "text": "b\n"}
        )

        nb = read_notebook(temp_notebook)
        assert len(nb.cells[0].outputs) == 2
        assert nb.cells[0].outputs[0]["text"] == "a\n"


class TestOutputTruncation:
    """Large plain-text outputs are truncated in MCP responses."""

    def test_large_stream_output_truncated(self):
        big = "x" * 10_000
        out = {"output_type": "stream", "name": "stdout", "text": big}

        filtered = OutputFilter.filter_single_output(out)

        assert len(filtered["text"]) < len(big)
        assert "truncated" in filtered["text"]
        # Head and tail are preserved
        assert filtered["text"].startswith("x")
        assert filtered["text"].endswith("x")
        # Original dict is not mutated (the notebook keeps full output)
        assert out["text"] == big

    def test_small_stream_output_untouched(self):
        out = {"output_type": "stream", "name": "stdout", "text": "hello\n"}
        assert OutputFilter.filter_single_output(out) is out

    def test_large_error_traceback_truncated(self):
        traceback = [f"frame {i}: something went wrong" for i in range(500)]
        out = {
            "output_type": "error",
            "ename": "RuntimeError",
            "evalue": "boom",
            "traceback": traceback,
        }

        filtered = OutputFilter.filter_single_output(out)

        assert sum(len(line) for line in filtered["traceback"]) < sum(
            len(line) for line in traceback
        )
        assert any("truncated" in line for line in filtered["traceback"])
        assert out["traceback"] == traceback  # original not mutated

    def test_large_text_plain_result_truncated(self):
        big_repr = "row\n" * 5_000
        out = {
            "output_type": "execute_result",
            "data": {"text/plain": big_repr},
            "metadata": {},
            "execution_count": 1,
        }

        filtered = OutputFilter.filter_single_output(out)

        assert len(filtered["data"]["text/plain"]) < len(big_repr)
        assert "truncated" in filtered["data"]["text/plain"]
        assert out["data"]["text/plain"] == big_repr  # original not mutated


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
