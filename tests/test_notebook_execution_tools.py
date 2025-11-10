"""Test notebook execution tools functionality - refactored for modular architecture."""

import asyncio
from datetime import datetime
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

# Import from new modular structure
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.notebook_execution_tools import (
    execute_notebook_code,
    get_server_context,
    list_notebook_kernels,
    modify_notebook_cells,
    query_notebook,
    read_cell,
    set_server_context,
    setup_notebook,
    shutdown_notebook,
    switch_notebook_kernel,
)
from jupyter_mcp_executor.notebook_io import read_notebook, write_notebook
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.path_utils import normalize_path
from jupyter_mcp_executor.server_context import _server_context
from jupyter_mcp_executor.validation import validate_cell_index, validate_code_cell


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
        self._client = MockKernelClient()

    def client(self):
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

    async def wait_for_ready(self, timeout=10):  # noqa: ARG002
        """Mock wait for ready."""
        await asyncio.sleep(0.01)

    def execute(self, _code):
        """Mock execute - prepares shell reply with execution_count."""
        msg_id = f"msg-id-{self.execution_count}"
        self._current_msg_id = msg_id

        # Queue the execute_reply for shell channel
        self._shell_messages.append({
            "header": {
                "msg_type": "execute_reply",
                "msg_id": f"reply-{self.execution_count}"
            },
            "parent_header": {"msg_id": msg_id},
            "content": {
                "status": "ok",
                "execution_count": self.execution_count
            }
        })

        self.execution_count += 1
        return msg_id

    async def get_shell_msg(self, timeout=None):  # noqa: ARG002
        """Mock get shell message - returns execute_reply."""
        await asyncio.sleep(0.01)

        if self._shell_messages:
            return self._shell_messages.pop(0)

        msg = "No shell messages available"
        raise asyncio.TimeoutError(msg)

    async def get_iopub_msg(self, timeout=None):  # noqa: ARG002
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
            "python3": {
                "spec": {
                    "display_name": "Python 3",
                    "language": "python"
                }
            },
            "ir": {
                "spec": {
                    "display_name": "R",
                    "language": "R"
                }
            }
        }


class MockServerApp:
    """Mock server app for testing."""

    def __init__(self):
        self.kernel_manager = MockKernelManager()
        self.kernel_spec_manager = MockKernelSpecManager()
        self.session_manager = MockSessionManager()
        self.root_dir = str(Path.cwd())


class MockSessionManager:
    """Mock session manager."""

    def __init__(self):
        """Initialize with empty sessions list."""
        self.sessions = []

    async def list_sessions(self):
        """Mock list sessions - returns list like real Jupyter Server."""
        return list(self.sessions)

    def add_session(self, notebook_path, kernel_id, kernel_name="python3"):
        """Add a mock session for testing."""
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
                "connections": 1
            }
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
def mock_serverapp():
    """Create a mock server app."""
    return MockServerApp()


@pytest.fixture(autouse=True)
def reset_context():
    """Reset global context before each test."""
    # Reset server context
    _server_context["serverapp"] = None

    # Reset kernel tracker
    tracker = get_kernel_tracker()
    tracker._kernels = {}

    yield

    # Clean up after test
    _server_context["serverapp"] = None
    tracker._kernels = {}


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

    def test_validate_cell_index(self, temp_notebook):
        """Test cell index validation."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))

        # Valid index
        validate_cell_index(nb, 0)  # Should not raise

        # Invalid index
        with pytest.raises(IndexError, match="Cannot access cell at index 5"):
            validate_cell_index(nb, 5)

    def test_validate_code_cell(self, temp_notebook):
        """Test code cell validation."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        nb.cells.append(new_markdown_cell("# Header"))

        # Valid code cell
        validate_code_cell(nb.cells[0], 0)  # Should not raise

        # Invalid - markdown cell
        with pytest.raises(ValueError, match="not a code cell"):
            validate_code_cell(nb.cells[1], 1)

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


class TestExecuteNotebookCode:
    """Test execute_notebook_code function."""

    @pytest.mark.asyncio
    async def test_execute_cell_by_position_no_context(self, temp_notebook):
        """Test execute without server context."""
        # Add a code cell first
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("print('test')"))
        write_notebook(temp_notebook, nb)

        # ExecutionEngine checks server context and returns error dict
        result = await execute_notebook_code(
            temp_notebook,
            "execute_cell",
            position_index=0
        )
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

        result = await execute_notebook_code(
            temp_notebook,
            "execute_cell",
            position_index=0
        )

        assert result["status"] == "ok"
        assert "execution_count" in result

    @pytest.mark.asyncio
    async def test_install_packages(self, temp_notebook, mock_serverapp):
        """Test installing packages."""
        set_server_context(mock_serverapp)

        result = await execute_notebook_code(
            temp_notebook,
            "install_packages",
            package_names="numpy pandas"
        )

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_invalid_execution_type(self, temp_notebook):
        """Test invalid execution type."""
        with pytest.raises(ValueError, match="Invalid execution_type"):
            await execute_notebook_code(
                temp_notebook,
                "invalid_type"
            )


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
    async def test_setup_notebook_reuses_existing_kernel(self, temp_notebook, mock_serverapp):
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
        assert result["reused_kernel"] is True
        assert "reused existing kernel" in result["message"]

        # Verify no new kernel was started (should still have only 1 kernel)
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_starts_new_when_no_session(self, temp_notebook, mock_serverapp):
        """Test that setup_notebook starts a new kernel when no session exists."""
        set_server_context(mock_serverapp)

        # Call setup_notebook without any existing session
        result = await setup_notebook(temp_notebook, kernel_name="python3")

        # Verify a new kernel was started
        assert result["kernel_id"] is not None
        assert result["reused_kernel"] is False
        assert "reused" not in result["message"]

        # Verify a kernel was created
        assert len(mock_serverapp.kernel_manager.kernels) == 1

    @pytest.mark.asyncio
    async def test_setup_notebook_force_new_kernel(self, temp_notebook, mock_serverapp):
        """Test force_new_kernel parameter creates a new kernel even when one exists."""
        set_server_context(mock_serverapp)

        # Simulate a notebook already open with a kernel
        existing_kernel_id = await mock_serverapp.kernel_manager.start_kernel("python3")
        normalized_path = normalize_path(temp_notebook)
        mock_serverapp.session_manager.add_session(
            normalized_path, existing_kernel_id, "python3"
        )

        # Call setup_notebook with force_new_kernel=True
        result = await setup_notebook(temp_notebook, kernel_name="python3", force_new_kernel=True)

        # Verify a NEW kernel was started (not the existing one)
        assert result["kernel_id"] != existing_kernel_id
        assert result["reused_kernel"] is False

        # Verify a new kernel was created (should now have 2 kernels)
        assert len(mock_serverapp.kernel_manager.kernels) == 2

    @pytest.mark.asyncio
    async def test_setup_notebook_handles_dead_kernel(self, temp_notebook, mock_serverapp):
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
        assert result["reused_kernel"] is False

        # Verify a new kernel was created
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
        assert result["kernel_id"] is None

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


class TestModifyNotebookCells:
    """Test modify_notebook_cells function."""

    @pytest.mark.asyncio
    async def test_add_code_cell_no_execute(self, temp_notebook):
        """Test adding a code cell without execution."""
        result = await modify_notebook_cells(
            temp_notebook, "add_code", "print('hello')", execute=False
        )
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

        result = await modify_notebook_cells(
            temp_notebook, "add_code", "print('hello')", execute=True
        )
        assert result["status"] == "ok"
        assert "execution_count" in result

    @pytest.mark.asyncio
    async def test_add_markdown_cell(self, temp_notebook):
        """Test adding a markdown cell."""
        result = await modify_notebook_cells(
            temp_notebook, "add_markdown", "# Title"
        )
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
        await modify_notebook_cells(temp_notebook, "add_code", "x = 1", execute=False)

        # Then edit it
        result = await modify_notebook_cells(
            temp_notebook, "edit_code", "x = 2", position_index=0, execute=True
        )
        assert result["status"] == "ok"

        # Verify edit
        nb = read_notebook(temp_notebook)
        assert nb.cells[0].source == "x = 2"

    @pytest.mark.asyncio
    async def test_edit_markdown_cell(self, temp_notebook):
        """Test editing a markdown cell."""
        # First add a cell
        await modify_notebook_cells(temp_notebook, "add_markdown", "# Old")

        # Then edit it
        result = await modify_notebook_cells(
            temp_notebook, "edit_markdown", "# New", position_index=0
        )
        assert "message" in result

        # Verify edit
        nb = read_notebook(temp_notebook)
        assert nb.cells[0].source == "# New"

    @pytest.mark.asyncio
    async def test_delete_cell(self, temp_notebook):
        """Test deleting a cell."""
        # First add a cell
        await modify_notebook_cells(temp_notebook, "add_code", "x = 1", execute=False)

        # Then delete it
        result = await modify_notebook_cells(
            temp_notebook, "delete", position_index=0
        )
        assert "message" in result

        # Verify cell was deleted
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 0

    @pytest.mark.asyncio
    async def test_invalid_operation(self, temp_notebook):
        """Test invalid operation."""
        with pytest.raises(ValueError, match="Invalid operation"):
            await modify_notebook_cells(temp_notebook, "invalid_op")

    @pytest.mark.asyncio
    async def test_add_code_at_position(self, temp_notebook):
        """Test adding code cell at specific position."""
        # Add two cells
        await modify_notebook_cells(temp_notebook, "add_code", "x = 1", execute=False)
        await modify_notebook_cells(temp_notebook, "add_code", "x = 3", execute=False)

        # Insert at position 1 (middle)
        await modify_notebook_cells(
            temp_notebook, "add_code", "x = 2", position_index=1, execute=False
        )

        # Verify order
        nb = read_notebook(temp_notebook)
        assert len(nb.cells) == 3
        assert nb.cells[0].source == "x = 1"
        assert nb.cells[1].source == "x = 2"
        assert nb.cells[2].source == "x = 3"


class TestQueryNotebook:
    """Test query_notebook function."""

    @pytest.mark.asyncio
    async def test_query_view_source_all_cells(self, temp_notebook):
        """Test viewing all cells."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        nb.cells.append(new_markdown_cell("# Header"))
        write_notebook(temp_notebook, nb)

        result = await query_notebook(temp_notebook, "view_source")
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_query_view_source_by_position(self, temp_notebook):
        """Test viewing cell by position."""
        nb = read_notebook(temp_notebook)
        nb.cells.append(new_code_cell("x = 1"))
        write_notebook(temp_notebook, nb)

        result = await query_notebook(temp_notebook, "view_source", position_index=0)
        assert isinstance(result, dict)
        assert result["source"] == "x = 1"

    @pytest.mark.asyncio
    async def test_query_view_source_by_execution_count(self, temp_notebook):
        """Test viewing cell by execution count."""
        nb = read_notebook(temp_notebook)
        cell = new_code_cell("x = 1")
        cell.execution_count = 5
        nb.cells.append(cell)
        write_notebook(temp_notebook, nb)

        result = await query_notebook(temp_notebook, "view_source", execution_count=5)
        assert isinstance(result, dict)
        assert result["source"] == "x = 1"
        assert result["execution_count"] == 5

    @pytest.mark.asyncio
    async def test_query_check_server(self, mock_serverapp):
        """Test checking server status."""
        set_server_context(mock_serverapp)
        result = await query_notebook("dummy.ipynb", "check_server")
        assert "running" in result.lower()

    @pytest.mark.asyncio
    async def test_query_list_sessions(self, mock_serverapp):
        """Test listing sessions."""
        set_server_context(mock_serverapp)
        result = await query_notebook("dummy.ipynb", "list_sessions")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_list_kernels(self, mock_serverapp):
        """Test listing kernel specs."""
        set_server_context(mock_serverapp)
        result = await query_notebook("dummy.ipynb", "list_kernels")
        assert "kernelspecs" in result

    @pytest.mark.asyncio
    async def test_query_get_position_index(self, temp_notebook):
        """Test getting position index by execution count."""
        nb = read_notebook(temp_notebook)
        cell = new_code_cell("x = 1")
        cell.execution_count = 10
        nb.cells.append(cell)
        write_notebook(temp_notebook, nb)

        result = await query_notebook(
            temp_notebook, "get_position_index", execution_count=10
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_query_invalid_type(self, temp_notebook):
        """Test invalid query type."""
        with pytest.raises(ValueError, match="Invalid query_type"):
            await query_notebook(temp_notebook, "invalid_type")


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
        {
            "output_type": "stream",
            "name": "stdout",
            "text": "Hello World\n"
        },
        {
            "output_type": "display_data",
            "data": {
                "text/plain": "Plot",
                "image/png": "iVBORw0KGgoAAAANS..." * 1000  # Large base64 data
            }
        },
        {
            "output_type": "execute_result",
            "data": {
                "text/html": "<div>Large HTML content...</div>" * 100
            },
            "execution_count": 1
        }
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
        await modify_notebook_cells(str(nb_path), "add_markdown", "# My Notebook")

        # 3. Add and execute code
        exec_result = await modify_notebook_cells(
            str(nb_path), "add_code", "x = 42", execute=True
        )
        assert exec_result["status"] == "ok"

        # 4. Query notebook
        cells = await query_notebook(str(nb_path), "view_source")
        assert len(cells) == 2
        assert cells[0]["cell_type"] == "markdown"
        assert cells[1]["cell_type"] == "code"

        # 5. Read specific cell
        cell = await read_cell(str(nb_path), position_index=1)
        assert cell["source"] == "x = 42"

        # 6. Shutdown
        await shutdown_notebook(str(nb_path))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
