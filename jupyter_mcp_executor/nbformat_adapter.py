"""Adapter for notebooks not open in JupyterLab (saved via the contents manager)."""

import logging
from typing import Any

from nbformat.v4 import new_code_cell, new_markdown_cell

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.notebook_store import editing_notebook, load_notebook
from jupyter_mcp_executor.output_converter import convert_outputs_to_notebook_nodes
from jupyter_mcp_executor.ydoc_helpers import get_cell_index_by_id_nbformat

logger = logging.getLogger(__name__)


def _cell_to_content(cell: Any) -> CellContent:
    """Convert an nbformat cell node to CellContent."""
    # The contents manager marks code cells trusted/untrusted when loading;
    # that mark is not part of the notebook.
    metadata = {k: v for k, v in cell.metadata.items() if k != "trusted"}
    return CellContent(
        source=cell.source,
        cell_type=cell.cell_type,
        metadata=metadata,
        execution_count=getattr(cell, "execution_count", None),
        outputs=list(getattr(cell, "outputs", [])),
        cell_id=cell.get("id"),
        attachments=dict(cell.get("attachments") or {}),
    )


def _check_position(nb: Any, position: int) -> None:
    """Raise IndexError if position is out of range."""
    if position < 0 or position >= len(nb.cells):
        msg = f"Cell index {position} out of range (notebook has {len(nb.cells)} cells)"
        raise IndexError(msg)


def _metadata_without_trust(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Cell metadata from a caller, minus ``trusted``: only outputs of the
    tools' own executions may be trusted (see ``_trust_outputs``)."""
    return {k: v for k, v in (metadata or {}).items() if k != "trusted"}


def _trust_outputs(cell: Any) -> None:
    """Trust outputs the tools' own executions produced, as JupyterLab trusts
    the outputs of cells it runs. Saving signs the notebook only if every
    risky output is trusted, so others' untrusted outputs stay untrusted."""
    cell.metadata["trusted"] = True


class NbformatAdapter(NotebookAdapter):
    """Adapter for notebooks not open in JupyterLab (file-based editing).

    Reads and saves go through Jupyter's contents manager (see
    ``notebook_store``), so changes reach the disk immediately, signed,
    checkpointed and through save hooks, as a JupyterLab save would. Edits of
    one notebook are serialized; read-only operations never save.
    """

    def __init__(self, notebook_path: str):
        """Initialize with notebook path.

        Args:
            notebook_path: Normalized path to notebook file
        """
        self._notebook_path = notebook_path

    @property
    def is_collaborative(self) -> bool:
        """Nbformat adapter does not support real-time collaboration."""
        return False

    @property
    def notebook_path(self) -> str:
        """Path to the notebook file."""
        return self._notebook_path

    async def _read(self) -> Any:
        """Load the notebook (read-only: nothing is saved)."""
        return await load_notebook(self._notebook_path)

    async def read_cell(self, position: int) -> CellContent:
        """Read cell from notebook file (read-only, no write-back)."""
        nb = await self._read()
        _check_position(nb, position)
        return _cell_to_content(nb.cells[position])

    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add cell to notebook file."""
        async with editing_notebook(self._notebook_path) as nb:
            pos = len(nb.cells) if position is None else position
            if pos < 0 or pos > len(nb.cells):
                pos = len(nb.cells)

            source_str = cell_content.get_source_as_string()
            if cell_content.cell_type == "code":
                cell = new_code_cell(
                    source=source_str,
                    metadata=_metadata_without_trust(cell_content.metadata),
                )
                if cell_content.execution_count is not None:
                    cell.execution_count = cell_content.execution_count
                if cell_content.outputs:
                    cell.outputs = convert_outputs_to_notebook_nodes(
                        cell_content.outputs
                    )
            else:
                cell = new_markdown_cell(
                    source=source_str,
                    metadata=_metadata_without_trust(cell_content.metadata),
                )

            nb.cells.insert(pos, cell)
            logger.info(
                f"Added {cell_content.cell_type} cell via nbformat at position {pos}"
            )
        return pos, cell.id

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in notebook file.

        For code cells the previous outputs and execution_count are replaced
        by the ones in `content` (typically empty), so stale outputs from the
        old source never linger on the edited cell.
        """
        async with editing_notebook(self._notebook_path) as nb:
            _check_position(nb, position)

            cell = nb.cells[position]
            cell.source = content.get_source_as_string()

            if content.metadata:
                cell.metadata = _metadata_without_trust(content.metadata)

            if cell.cell_type == "code":
                cell.execution_count = content.execution_count
                cell.outputs = convert_outputs_to_notebook_nodes(content.outputs or [])
                # The trust mark from loading vouched for the old outputs, not
                # for these: the notary judges them on their own (empty ones
                # are always fine, so the notebook stays signed).
                cell.metadata.pop("trusted", None)

            logger.info(
                f"Edited {content.cell_type} cell via nbformat at position {position}"
            )

    async def delete_cell(self, position: int) -> None:
        """Delete cell from notebook file."""
        async with editing_notebook(self._notebook_path) as nb:
            _check_position(nb, position)
            nb.cells.pop(position)
            logger.info(f"Deleted cell via nbformat at position {position}")

    async def update_cell_outputs(
        self,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]] | None,
    ) -> None:
        """Write a cell's execution count and the outputs of its execution."""
        async with editing_notebook(self._notebook_path) as nb:
            _check_position(nb, position)

            cell = nb.cells[position]

            if execution_count is not None:
                cell.execution_count = execution_count

            if outputs is not None:
                cell.outputs = convert_outputs_to_notebook_nodes(outputs)
                _trust_outputs(cell)

            logger.debug(f"Updated cell {position} outputs via nbformat")

    async def append_cell_output(self, position: int, output: dict[str, Any]) -> None:
        """Append a single output to a cell in the notebook file.

        Closed notebooks have no live viewer, so the engine does not stream
        through this path; it exists for protocol completeness. The cell's
        trust is left as it is: trusting it would also trust the outputs it
        already had.
        """
        async with editing_notebook(self._notebook_path) as nb:
            _check_position(nb, position)

            cell = nb.cells[position]
            existing = list(getattr(cell, "outputs", []) or [])
            cell.outputs = existing + convert_outputs_to_notebook_nodes([output])

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from notebook file (read-only, no write-back)."""
        nb = await self._read()
        return [_cell_to_content(cell) for cell in nb.cells]

    async def get_cell_count(self) -> int:
        """Get cell count from notebook file (read-only, no write-back)."""
        nb = await self._read()
        return len(nb.cells)

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in notebook file (read-only, no write-back)."""
        nb = await self._read()
        position = get_cell_index_by_id_nbformat(nb, cell_id)
        if position is None:
            return None
        return _cell_to_content(nb.cells[position]), position

    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count (read-only, no write-back)."""
        nb = await self._read()
        for i, cell in enumerate(nb.cells):
            if (
                cell.cell_type == "code"
                and getattr(cell, "execution_count", None) == execution_count
            ):
                return _cell_to_content(cell), i
        return None
