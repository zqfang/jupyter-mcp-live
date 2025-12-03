"""Tests for persistence utilities."""

import tempfile
from pathlib import Path

import nbformat
import pytest
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.persistence_utils import (
    persist_cell_addition_to_disk,
    persist_cell_deletion_to_disk,
    persist_cell_edit_to_disk,
    persist_cell_outputs_to_disk,
)


@pytest.fixture
def temp_notebook():
    """Create a temporary notebook file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False) as f:
        nb = new_notebook(
            cells=[new_code_cell("print('hello')"), new_markdown_cell("# Test")]
        )
        nbformat.write(nb, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def empty_notebook():
    """Create an empty temporary notebook file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False) as f:
        nb = new_notebook(cells=[])
        nbformat.write(nb, f)
        temp_path = f.name

    yield temp_path

    # Cleanup
    Path(temp_path).unlink(missing_ok=True)


class TestPersistCellAddition:
    """Test persist_cell_addition_to_disk."""

    @pytest.mark.asyncio
    async def test_persist_code_cell_addition(self, empty_notebook):
        """Test persisting code cell addition to disk."""
        cell_content = CellContent(
            source="print('test')", cell_type="code", execution_count=None, outputs=[]
        )

        await persist_cell_addition_to_disk(empty_notebook, 0, cell_content)

        # Verify cell was added to disk
        nb = nbformat.read(empty_notebook, as_version=4)
        assert len(nb.cells) == 1
        assert nb.cells[0].cell_type == "code"
        assert nb.cells[0].source == "print('test')"

    @pytest.mark.asyncio
    async def test_persist_markdown_cell_addition(self, empty_notebook):
        """Test persisting markdown cell addition to disk."""
        cell_content = CellContent(source="# Header", cell_type="markdown")

        await persist_cell_addition_to_disk(empty_notebook, 0, cell_content)

        # Verify cell was added to disk
        nb = nbformat.read(empty_notebook, as_version=4)
        assert len(nb.cells) == 1
        assert nb.cells[0].cell_type == "markdown"
        assert nb.cells[0].source == "# Header"

    @pytest.mark.asyncio
    async def test_persist_cell_addition_with_metadata(self, empty_notebook):
        """Test persisting cell addition with metadata."""
        cell_content = CellContent(
            source="x = 1", cell_type="code", metadata={"custom": "value"}
        )

        await persist_cell_addition_to_disk(empty_notebook, 0, cell_content)

        # Verify metadata was preserved
        nb = nbformat.read(empty_notebook, as_version=4)
        assert nb.cells[0].metadata["custom"] == "value"

    @pytest.mark.asyncio
    async def test_persist_cell_addition_at_end(self, temp_notebook):
        """Test persisting cell addition at the end."""
        cell_content = CellContent(source="new cell", cell_type="code")

        # Read initial state
        nb_before = nbformat.read(temp_notebook, as_version=4)
        initial_count = len(nb_before.cells)

        await persist_cell_addition_to_disk(temp_notebook, initial_count, cell_content)

        # Verify cell was added at end
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == initial_count + 1
        assert nb.cells[-1].source == "new cell"

    @pytest.mark.asyncio
    async def test_persist_cell_addition_in_middle(self, temp_notebook):
        """Test persisting cell addition in the middle."""
        cell_content = CellContent(source="middle cell", cell_type="markdown")

        await persist_cell_addition_to_disk(temp_notebook, 1, cell_content)

        # Verify cell was inserted at position 1
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 3
        assert nb.cells[1].source == "middle cell"
        assert nb.cells[1].cell_type == "markdown"

    @pytest.mark.asyncio
    async def test_invalid_position_silent_failure(self, temp_notebook):
        """Test that invalid position fails silently without corrupting notebook."""
        cell_content = CellContent(source="test", cell_type="code")

        # Try to add at invalid position - should not raise exception
        await persist_cell_addition_to_disk(temp_notebook, 999, cell_content)

        # Verify notebook wasn't corrupted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 2  # Original cells unchanged

    @pytest.mark.asyncio
    async def test_persist_cell_addition_with_outputs(self, empty_notebook):
        """Test persisting code cell with outputs."""
        cell_content = CellContent(
            source="print('hello')",
            cell_type="code",
            execution_count=1,
            outputs=[{"output_type": "stream", "name": "stdout", "text": ["hello\n"]}],
        )

        await persist_cell_addition_to_disk(empty_notebook, 0, cell_content)

        # Verify outputs were saved
        nb = nbformat.read(empty_notebook, as_version=4)
        assert nb.cells[0].execution_count == 1
        assert len(nb.cells[0].outputs) == 1
        assert nb.cells[0].outputs[0]["output_type"] == "stream"


class TestPersistCellEdit:
    """Test persist_cell_edit_to_disk."""

    @pytest.mark.asyncio
    async def test_persist_cell_edit(self, temp_notebook):
        """Test persisting cell edit to disk."""
        cell_content = CellContent(source="print('edited')", cell_type="code")

        await persist_cell_edit_to_disk(temp_notebook, 0, cell_content)

        # Verify cell was edited
        nb = nbformat.read(temp_notebook, as_version=4)
        assert nb.cells[0].source == "print('edited')"

    @pytest.mark.asyncio
    async def test_persist_cell_edit_with_metadata(self, temp_notebook):
        """Test persisting cell edit with metadata update."""
        cell_content = CellContent(
            source="new content", cell_type="code", metadata={"edited": True}
        )

        await persist_cell_edit_to_disk(temp_notebook, 0, cell_content)

        # Verify metadata was updated
        nb = nbformat.read(temp_notebook, as_version=4)
        assert nb.cells[0].metadata["edited"] is True

    @pytest.mark.asyncio
    async def test_persist_markdown_cell_edit(self, temp_notebook):
        """Test persisting markdown cell edit."""
        cell_content = CellContent(source="## Edited Header", cell_type="markdown")

        await persist_cell_edit_to_disk(temp_notebook, 1, cell_content)

        # Verify markdown cell was edited
        nb = nbformat.read(temp_notebook, as_version=4)
        assert nb.cells[1].source == "## Edited Header"

    @pytest.mark.asyncio
    async def test_invalid_position_silent_failure(self, temp_notebook):
        """Test that invalid position fails silently."""
        cell_content = CellContent(source="test", cell_type="code")

        # Should not raise exception
        await persist_cell_edit_to_disk(temp_notebook, 999, cell_content)

        # Verify notebook wasn't corrupted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 2  # Original cells unchanged


class TestPersistCellDeletion:
    """Test persist_cell_deletion_to_disk."""

    @pytest.mark.asyncio
    async def test_persist_cell_deletion(self, temp_notebook):
        """Test persisting cell deletion to disk."""
        # Read initial state
        nb_before = nbformat.read(temp_notebook, as_version=4)
        assert len(nb_before.cells) == 2

        await persist_cell_deletion_to_disk(temp_notebook, 0)

        # Verify cell was deleted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 1
        assert nb.cells[0].cell_type == "markdown"  # Second cell remains

    @pytest.mark.asyncio
    async def test_persist_deletion_of_last_cell(self, temp_notebook):
        """Test persisting deletion of the last cell."""
        await persist_cell_deletion_to_disk(temp_notebook, 1)

        # Verify correct cell was deleted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 1
        assert nb.cells[0].cell_type == "code"  # First cell remains

    @pytest.mark.asyncio
    async def test_invalid_position_silent_failure(self, temp_notebook):
        """Test that invalid position fails silently."""
        # Should not raise exception
        await persist_cell_deletion_to_disk(temp_notebook, 999)

        # Verify notebook wasn't corrupted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 2  # Original cells unchanged


class TestPersistCellOutputs:
    """Test persist_cell_outputs_to_disk."""

    @pytest.mark.asyncio
    async def test_persist_outputs(self, temp_notebook):
        """Test persisting cell outputs to disk."""
        outputs = [
            {"output_type": "stream", "name": "stdout", "text": ["output\n"]},
            {
                "output_type": "execute_result",
                "data": {"text/plain": ["42"]},
                "execution_count": 1,
            },
        ]

        await persist_cell_outputs_to_disk(temp_notebook, 0, 1, outputs)

        # Verify outputs were persisted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert nb.cells[0].execution_count == 1
        assert len(nb.cells[0].outputs) == 2
        assert nb.cells[0].outputs[0]["output_type"] == "stream"

    @pytest.mark.asyncio
    async def test_persist_execution_count_only(self, temp_notebook):
        """Test persisting only execution count without outputs."""
        await persist_cell_outputs_to_disk(temp_notebook, 0, 5, [])

        # Verify execution count was updated
        nb = nbformat.read(temp_notebook, as_version=4)
        assert nb.cells[0].execution_count == 5

    @pytest.mark.asyncio
    async def test_persist_outputs_with_none_execution_count(self, temp_notebook):
        """Test persisting outputs without execution count."""
        outputs = [{"output_type": "stream", "name": "stdout", "text": ["test\n"]}]

        await persist_cell_outputs_to_disk(temp_notebook, 0, None, outputs)

        # Verify outputs were persisted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells[0].outputs) == 1

    @pytest.mark.asyncio
    async def test_invalid_position_silent_failure(self, temp_notebook):
        """Test that invalid position fails silently."""
        # Should not raise exception
        await persist_cell_outputs_to_disk(temp_notebook, 999, 1, [])

        # Verify notebook wasn't corrupted
        nb = nbformat.read(temp_notebook, as_version=4)
        assert len(nb.cells) == 2  # Original cells unchanged


class TestErrorHandling:
    """Test error handling in persistence utilities."""

    @pytest.mark.asyncio
    async def test_persist_to_nonexistent_file(self):
        """Test that nonexistent file fails silently."""
        cell_content = CellContent(source="test", cell_type="code")

        # Should not raise exception
        await persist_cell_addition_to_disk("/nonexistent/path.ipynb", 0, cell_content)

    @pytest.mark.asyncio
    async def test_persist_edit_to_nonexistent_file(self):
        """Test that edit to nonexistent file fails silently."""
        cell_content = CellContent(source="test", cell_type="code")

        # Should not raise exception
        await persist_cell_edit_to_disk("/nonexistent/path.ipynb", 0, cell_content)

    @pytest.mark.asyncio
    async def test_persist_deletion_to_nonexistent_file(self):
        """Test that deletion on nonexistent file fails silently."""
        # Should not raise exception
        await persist_cell_deletion_to_disk("/nonexistent/path.ipynb", 0)

    @pytest.mark.asyncio
    async def test_persist_outputs_to_nonexistent_file(self):
        """Test that output persistence on nonexistent file fails silently."""
        # Should not raise exception
        await persist_cell_outputs_to_disk("/nonexistent/path.ipynb", 0, 1, [])
