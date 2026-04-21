"""Test suite for refactored architecture - import verification."""

import pytest


def test_import_main_module():
    """Test that main module imports correctly."""
    import jupyter_mcp_executor.notebook_execution_tools as tools  # noqa: PLC0415

    # Verify all MCP-exported functions exist
    assert hasattr(tools, 'list_notebook_sessions')
    assert hasattr(tools, 'list_notebook_kernels')
    assert hasattr(tools, 'setup_notebook')
    assert hasattr(tools, 'switch_notebook_kernel')
    assert hasattr(tools, 'shutdown_notebook')
    assert hasattr(tools, 'execute_notebook_code')
    assert hasattr(tools, 'modify_notebook_cells')
    assert hasattr(tools, 'query_notebook')
    assert hasattr(tools, 'read_cell')
    assert hasattr(tools, 'set_server_context')
    assert hasattr(tools, 'get_server_context')


def test_import_services():
    """Test that all service modules import correctly."""
    from jupyter_mcp_executor.cell_operations import CellOperations  # noqa: PLC0415
    from jupyter_mcp_executor.execution_engine import ExecutionEngine  # noqa: PLC0415
    from jupyter_mcp_executor.kernel_manager import KernelManager  # noqa: PLC0415
    from jupyter_mcp_executor.query_service import QueryService  # noqa: PLC0415

    # Verify classes can be instantiated
    execution_engine = ExecutionEngine()
    assert execution_engine is not None

    cell_ops = CellOperations(execution_engine)
    assert cell_ops is not None

    kernel_mgr = KernelManager()
    assert kernel_mgr is not None

    query_svc = QueryService()
    assert query_svc is not None


def test_import_adapters():
    """Test that adapter modules import correctly."""
    from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter  # noqa: PLC0415
    from jupyter_mcp_executor.notebook_adapter import (  # noqa: PLC0415
        NotebookAdapter,
        get_notebook_adapter,
    )
    from jupyter_mcp_executor.ydoc_adapter import YDocAdapter  # noqa: PLC0415

    # Verify classes exist
    assert NotebookAdapter is not None
    assert YDocAdapter is not None
    assert NbformatAdapter is not None
    assert get_notebook_adapter is not None


def test_import_models():
    """Test that model modules import correctly."""
    from jupyter_mcp_executor.notebook_models import (  # noqa: PLC0415
        CellIdentifier,
    )

    # Test CellIdentifier validation
    with pytest.raises(ValueError, match="exactly ONE"):
        CellIdentifier()  # No identifiers provided

    with pytest.raises(ValueError, match="exactly ONE"):
        CellIdentifier(position_index=0, cell_id="abc")  # Two provided

    # Valid identifiers
    id1 = CellIdentifier(position_index=0)
    assert id1.identifier_type == "position_index"

    id2 = CellIdentifier(cell_id="abc")
    assert id2.identifier_type == "cell_id"

    id3 = CellIdentifier(execution_count=1)
    assert id3.identifier_type == "execution_count"


def test_import_utilities():
    """Test that utility modules import correctly."""
    from jupyter_mcp_executor.notebook_io import (  # noqa: PLC0415
        NotebookContext,
        read_notebook,
        write_notebook,
    )
    from jupyter_mcp_executor.output_converter import (  # noqa: PLC0415
        convert_outputs_to_notebook_nodes,
    )
    from jupyter_mcp_executor.output_filter import OutputFilter  # noqa: PLC0415
    from jupyter_mcp_executor.path_utils import normalize_path  # noqa: PLC0415
    from jupyter_mcp_executor.validation import (  # noqa: PLC0415
        validate_cell_index,
        validate_code_cell,
    )

    # Verify functions exist
    assert normalize_path is not None
    assert validate_cell_index is not None
    assert validate_code_cell is not None
    assert OutputFilter is not None
    assert convert_outputs_to_notebook_nodes is not None
    assert NotebookContext is not None
    assert read_notebook is not None
    assert write_notebook is not None


def test_import_config():
    """Test that config module imports correctly."""
    from jupyter_mcp_executor.notebook_config import (  # noqa: PLC0415
        EXECUTION_CONFIG,
        LANGUAGE_PATTERNS,
        OUTPUT_FILTER_CONFIG,
        KernelLanguage,
    )

    # Verify constants exist
    assert EXECUTION_CONFIG is not None
    assert OUTPUT_FILTER_CONFIG is not None
    assert KernelLanguage is not None
    assert LANGUAGE_PATTERNS is not None

    # Verify LANGUAGE_PATTERNS structure
    assert "python" in LANGUAGE_PATTERNS
    assert "R" in LANGUAGE_PATTERNS


def test_kernel_tracker():
    """Test kernel tracker functionality."""
    from jupyter_mcp_executor.kernel_tracker import (  # noqa: PLC0415
        KernelTracker,
        get_kernel_tracker,
    )

    tracker = KernelTracker()

    # Test tracking
    tracker.track("/path/to/notebook.ipynb", "kernel-123")
    assert tracker.get("/path/to/notebook.ipynb") == "kernel-123"

    # Test untracking
    tracker.untrack("/path/to/notebook.ipynb")
    assert tracker.get("/path/to/notebook.ipynb") is None

    # Test singleton
    singleton = get_kernel_tracker()
    assert singleton is not None


def test_output_filter():
    """Test output filtering logic."""
    from jupyter_mcp_executor.output_filter import OutputFilter  # noqa: PLC0415

    # Test should_filter logic
    assert not OutputFilter.should_filter(["text/plain"], 100)
    assert OutputFilter.should_filter(["image/png"], 5000)

    # Test filter_single_output
    output = {
        "output_type": "stream",
        "name": "stdout",
        "text": "Hello"
    }
    filtered = OutputFilter.filter_single_output(output)
    assert filtered == output  # Should not be filtered

    # Test placeholder creation
    placeholder = OutputFilter.create_placeholder(["image/png"])
    assert "Filtered" in placeholder["data"]["text/plain"]


def test_cell_content_model():
    """Test CellContent model."""
    from jupyter_mcp_executor.notebook_models import CellContent  # noqa: PLC0415

    # Test with string source
    cell1 = CellContent(source="print('hello')", cell_type="code")
    assert cell1.get_source_as_string() == "print('hello')"

    # Test with list source
    cell2 = CellContent(source=["line1\n", "line2"], cell_type="code")
    assert cell2.get_source_as_string() == "line1\nline2"


def test_execution_result_model():
    """Test ExecutionResult model."""
    from jupyter_mcp_executor.notebook_models import ExecutionResult  # noqa: PLC0415

    result = ExecutionResult(
        execution_count=1,
        outputs=[{"output_type": "stream", "text": "test"}],
        status="ok",
        kernel_id="kernel-123",
        started_new_kernel=True
    )

    # Test model_dump (Pydantic method)
    result_dict = result.model_dump(exclude_none=True)
    assert result_dict["execution_count"] == 1
    assert result_dict["started_new_kernel"] is True

    # Test to_filtered_dict (custom method)
    filtered_dict = result.to_filtered_dict()
    assert "outputs" in filtered_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
