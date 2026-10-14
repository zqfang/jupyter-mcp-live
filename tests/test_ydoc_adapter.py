"""Tests for YDocAdapter output operations against a real YNotebook.

Covers the live-streaming primitives (append_cell_output with stream
coalescing, clear_cell_outputs) and the execution_count-only update used
when streaming already delivered the outputs.
"""

import pytest

pytest.importorskip("jupyter_ydoc")

from jupyter_ydoc import YNotebook

from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.ydoc_adapter import YDocAdapter


async def make_adapter_with_code_cell() -> tuple[YDocAdapter, int]:
    """Create an adapter over a fresh YNotebook with one empty code cell."""
    adapter = YDocAdapter(YNotebook(), "/tmp/test_ydoc_adapter.ipynb")
    position, _cell_id = await adapter.add_cell(
        CellContent(source="print('x')", cell_type="code")
    )
    return adapter, position


class TestAppendCellOutput:
    """append_cell_output streams one output at a time into the YDoc."""

    @pytest.mark.asyncio
    async def test_appends_outputs_in_order(self):
        adapter, pos = await make_adapter_with_code_cell()

        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "hi\n"}
        )
        await adapter.append_cell_output(
            pos,
            {
                "output_type": "execute_result",
                "data": {"text/plain": "42"},
                "metadata": {},
                "execution_count": 1,
            },
        )

        cell = await adapter.read_cell(pos)
        assert [o["output_type"] for o in cell.outputs] == [
            "stream",
            "execute_result",
        ]

    @pytest.mark.asyncio
    async def test_consecutive_same_stream_coalesces(self):
        """stdout lines merge into one entry with in-place Text appends."""
        adapter, pos = await make_adapter_with_code_cell()

        for i in range(3):
            await adapter.append_cell_output(
                pos, {"output_type": "stream", "name": "stdout", "text": f"line{i}\n"}
            )

        cell = await adapter.read_cell(pos)
        assert len(cell.outputs) == 1
        assert cell.outputs[0]["text"] == "line0\nline1\nline2\n"

    @pytest.mark.asyncio
    async def test_different_stream_names_stay_separate(self):
        adapter, pos = await make_adapter_with_code_cell()

        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "out\n"}
        )
        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stderr", "text": "err\n"}
        )

        cell = await adapter.read_cell(pos)
        assert len(cell.outputs) == 2
        assert cell.outputs[1]["name"] == "stderr"

    @pytest.mark.asyncio
    async def test_stream_after_display_data_not_coalesced(self):
        adapter, pos = await make_adapter_with_code_cell()

        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "a\n"}
        )
        await adapter.append_cell_output(
            pos, {"output_type": "display_data", "data": {}, "metadata": {}}
        )
        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "b\n"}
        )

        cell = await adapter.read_cell(pos)
        assert len(cell.outputs) == 3

    @pytest.mark.asyncio
    async def test_out_of_range_raises(self):
        adapter, _pos = await make_adapter_with_code_cell()

        with pytest.raises(IndexError):
            await adapter.append_cell_output(
                99, {"output_type": "stream", "name": "stdout", "text": "x"}
            )


class TestEditCell:
    """edit_cell rewrites the live pycrdt Text source in place.

    Regression coverage for the real-YNotebook path: cells created via
    create_ycell store source as pycrdt.Text, which has no .extend()
    (that API was y_py's) — the mocked main suite never reaches this branch.
    """

    @pytest.mark.asyncio
    async def test_edit_replaces_source(self):
        adapter, pos = await make_adapter_with_code_cell()

        await adapter.edit_cell(
            pos, CellContent(source="x = 1\nprint(x)", cell_type="code")
        )

        cell = await adapter.read_cell(pos)
        assert cell.source == "x = 1\nprint(x)"

    @pytest.mark.asyncio
    async def test_edit_resets_outputs_and_execution_count(self):
        adapter, pos = await make_adapter_with_code_cell()
        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "stale\n"}
        )
        await adapter.update_cell_outputs(pos, 3, None)

        await adapter.edit_cell(pos, CellContent(source="y = 2", cell_type="code"))

        cell = await adapter.read_cell(pos)
        assert cell.outputs == []
        assert cell.execution_count is None

    @pytest.mark.asyncio
    async def test_edit_to_empty_source(self):
        adapter, pos = await make_adapter_with_code_cell()

        await adapter.edit_cell(pos, CellContent(source="", cell_type="code"))

        cell = await adapter.read_cell(pos)
        assert cell.source == ""

    @pytest.mark.asyncio
    async def test_edit_out_of_range_raises(self):
        adapter, _pos = await make_adapter_with_code_cell()

        with pytest.raises(IndexError):
            await adapter.edit_cell(99, CellContent(source="x", cell_type="code"))


class TestClearAndCountOnlyUpdate:
    """clear_cell_outputs and execution_count-only updates."""

    @pytest.mark.asyncio
    async def test_clear_cell_outputs_empties_outputs(self):
        adapter, pos = await make_adapter_with_code_cell()
        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "old\n"}
        )

        await adapter.clear_cell_outputs(pos)

        cell = await adapter.read_cell(pos)
        assert cell.outputs == []

    @pytest.mark.asyncio
    async def test_update_with_none_outputs_preserves_streamed_content(self):
        """The post-streaming reconcile sets the count without touching outputs."""
        adapter, pos = await make_adapter_with_code_cell()
        await adapter.append_cell_output(
            pos, {"output_type": "stream", "name": "stdout", "text": "kept\n"}
        )

        await adapter.update_cell_outputs(pos, 5, None)

        cell = await adapter.read_cell(pos)
        assert cell.execution_count == 5
        assert len(cell.outputs) == 1
        assert cell.outputs[0]["text"] == "kept\n"


class TestReadCellAttachments:
    """read_cell exposes markdown attachments (pasted images) from the YDoc."""

    @pytest.mark.asyncio
    async def test_markdown_attachments_are_read(self):
        adapter = YDocAdapter(YNotebook(), "/tmp/test_ydoc_adapter.ipynb")
        attachments = {"image.png": {"image/png": "iVBORw0KGgo="}}
        adapter.ydoc.append_cell(
            {
                "cell_type": "markdown",
                "source": "![image.png](attachment:image.png)",
                "metadata": {},
                "attachments": attachments,
                "id": "md-1",
            }
        )

        cell = await adapter.read_cell(0)

        assert cell.attachments == attachments

    @pytest.mark.asyncio
    async def test_cell_without_attachments_reads_empty(self):
        adapter, pos = await make_adapter_with_code_cell()

        cell = await adapter.read_cell(pos)

        assert cell.attachments == {}


class TestReadCellNormalization:
    """read_cell returns cells as jupyter_ydoc saves them (YNotebook.get_cell)."""

    @pytest.mark.asyncio
    async def test_numbers_from_yjs_are_ints(self):
        """Yjs stores numbers as floats: a cell run in JupyterLab must not
        report execution_count 5.0."""
        adapter, pos = await make_adapter_with_code_cell()
        ycell = adapter.ydoc.ycells[pos]
        ycell["execution_count"] = 5.0

        cell = await adapter.read_cell(pos)

        assert cell.execution_count == 5
        assert isinstance(cell.execution_count, int)

    @pytest.mark.asyncio
    async def test_cell_id_is_kept_for_nbformat_4_4(self):
        """get_cell() drops ids of nbformat <= 4.4 notebooks, as saving does;
        the tools still need them to find the cell again."""
        adapter, pos = await make_adapter_with_code_cell()
        adapter.ydoc._ymeta["nbformat"] = 4
        adapter.ydoc._ymeta["nbformat_minor"] = 4
        cell_id = str(adapter.ydoc.ycells[pos]["id"])

        cell = await adapter.read_cell(pos)

        assert cell.cell_id == cell_id


@pytest.mark.asyncio
async def test_rewriting_outputs_is_one_transaction():
    """clear + extend as two transactions shows browsers an empty output area
    between them: a flash on every frame of a redrawn progress display."""
    adapter, position = await make_adapter_with_code_cell()
    await adapter.append_cell_output(
        position, {"output_type": "stream", "name": "stdout", "text": "old\n"}
    )
    transactions = []
    subscription = adapter.ydoc.ydoc.observe(transactions.append)
    try:
        await adapter.update_cell_outputs(
            position, 3, [{"output_type": "stream", "name": "stdout", "text": "new\n"}]
        )
    finally:
        adapter.ydoc.ydoc.unobserve(subscription)

    assert len(transactions) == 1
