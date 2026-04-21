"""YDoc-based adapter for collaborative notebook editing."""

import logging
import uuid
from typing import Any

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.ydoc_helpers import (
    YDOC_AVAILABLE,
    YText,
    get_cell_index_by_id_ydoc,
)

logger = logging.getLogger(__name__)


class YDocAdapter(NotebookAdapter):
    """Adapter for notebooks open in JupyterLab (collaborative editing via YDoc).

    This adapter modifies the in-memory YDoc representation, which provides
    real-time collaboration and immediate visibility in JupyterLab.
    """

    def __init__(self, ydoc: Any, notebook_path: str):
        """Initialize with YNotebook instance.

        Args:
            ydoc: YNotebook instance from jupyter_ydoc
            notebook_path: Normalized path to notebook
        """
        if not YDOC_AVAILABLE:
            msg = "YDoc is not available (jupyter_ydoc not installed)"
            raise RuntimeError(msg)

        self.ydoc = ydoc
        self._notebook_path = notebook_path

    @property
    def is_collaborative(self) -> bool:
        """YDoc adapter supports real-time collaboration."""
        return True

    @property
    def notebook_path(self) -> str:
        """Path to the notebook file."""
        return self._notebook_path

    async def read_cell(self, position: int) -> CellContent:
        """Read cell from YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        # Use to_py() to recursively convert all pycrdt types (YText→str,
        # Map→dict, Array→list) into plain Python objects.
        ycell_py = ycell.to_py()

        return CellContent(
            source=str(ycell_py.get("source", "")),
            cell_type=str(ycell_py.get("cell_type", "code")),
            metadata=dict(ycell_py.get("metadata", {})),
            execution_count=ycell_py.get("execution_count"),
            outputs=list(ycell_py.get("outputs", [])),
            cell_id=str(ycell_py.get("id", "")),
        )

    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add cell to YDoc."""
        # Convert source to string
        source_str = cell_content.get_source_as_string()

        # Generate a single cell ID used for both YDoc and disk persistence.
        # This prevents duplication when JupyterLab syncs disk → YDoc on file
        # change: without a shared ID, YDoc and disk cells get different UUIDs
        # and the sync treats them as two distinct cells.
        cell_id = str(uuid.uuid4())

        # Build a plain-dict representation; create_ycell will wrap
        # source in YText, metadata in Map, and outputs in Array[Map] —
        # inserting a raw dict would cause AttributeError('dict has no .to_py')
        # when JupyterLab tries to save or read back the cell.
        cell_dict = {
            "id": cell_id,
            "cell_type": cell_content.cell_type,
            "source": source_str,
            "metadata": dict(cell_content.metadata),
        }

        if cell_content.cell_type == "code":
            cell_dict["outputs"] = list(cell_content.outputs)
            cell_dict["execution_count"] = cell_content.execution_count

        # Determine position
        if position is None:
            position = self.ydoc.cell_number

        # Validate position
        if position < 0 or position > self.ydoc.cell_number:
            position = self.ydoc.cell_number

        # Convert to proper pycrdt Map before inserting into the YDoc Array
        ycell = self.ydoc.create_ycell(cell_dict)
        self.ydoc.ycells.insert(position, ycell)
        logger.info(
            f"Added {cell_content.cell_type} cell via YDoc at position {position}"
        )

        # Set activeCellIndex in YDoc metadata (instant, no browser needed)
        self._set_active_cell_index(position)

        return position, cell_id

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        # Update source using YText for collaborative editing
        source_str = content.get_source_as_string()
        ysource = ycell.get("source")

        if YText and isinstance(ysource, YText):
            # Clear and update YText (preserves collaboration)
            ysource.clear()
            ysource.extend(source_str)
        else:
            # Fall back to direct assignment
            ycell["source"] = YText(source_str) if YText else source_str

        # Update metadata — must be a pycrdt Map, not a plain dict
        if content.metadata:
            from pycrdt import Map  # noqa: PLC0415
            ycell["metadata"] = Map(content.metadata)

        logger.info(f"Edited {content.cell_type} cell via YDoc at position {position}")

        # Set activeCellIndex in YDoc metadata (instant, no browser needed)
        self._set_active_cell_index(position)

    def _set_active_cell_index(self, position: int) -> None:
        """Set activeCellIndex in YDoc metadata so JupyterLab activates the cell.

        Instant, in-memory hint — no browser required.

        Best-effort; older jupyter_ydoc versions may not expose ``ymeta``.
        """
        try:
            ymeta = getattr(self.ydoc, "ymeta", None)
            if ymeta is not None:
                ymeta["activeCellIndex"] = position
        except Exception:
            pass  # Non-critical: scroll hint only

    async def delete_cell(self, position: int) -> None:
        """Delete cell from YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        self.ydoc.ycells.pop(position)
        logger.info(f"Deleted cell via YDoc at position {position}")

    async def update_cell_outputs(
        self, position: int, execution_count: int | None, outputs: list[dict[str, Any]]
    ) -> None:
        """Update cell outputs in YDoc."""
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        if execution_count is not None:
            ycell["execution_count"] = execution_count

        if outputs is not None:
            # outputs must be stored as a pycrdt Array[Map], not a plain list.
            # Assigning a raw list breaks YNotebook.get_cell() → to_py() on save.
            from pycrdt import Array, Map, Text  # noqa: PLC0415
            output_maps = []
            for output in outputs:
                out = dict(output)
                if out.get("output_type") == "stream":
                    text = out.get("text", "")
                    out["text"] = Text("".join(text) if isinstance(text, list) else text)
                output_maps.append(Map(out))

            youtputs = ycell.get("outputs")
            if isinstance(youtputs, Array):
                youtputs.clear()
                if output_maps:
                    youtputs.extend(output_maps)
            else:
                ycell["outputs"] = Array(output_maps)

        logger.debug(
            f"Updated cell {position} outputs in YDoc (execution_count={execution_count})"
        )

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from YDoc."""
        cells = []
        for i in range(self.ydoc.cell_number):
            cells.append(await self.read_cell(i))
        return cells

    async def get_cell_count(self) -> int:
        """Get cell count from YDoc."""
        return self.ydoc.cell_number

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in YDoc."""
        position = get_cell_index_by_id_ydoc(self.ydoc, cell_id)
        if position is None:
            return None

        cell = await self.read_cell(position)
        return (cell, position)

    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count in YDoc."""
        for i in range(self.ydoc.cell_number):
            ycell = self.ydoc.ycells[i]
            if (
                ycell.get("cell_type") == "code"
                and ycell.get("execution_count") == execution_count
            ):
                cell = await self.read_cell(i)
                return (cell, i)

        return None
