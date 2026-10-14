"""YDoc-based adapter for collaborative notebook editing."""

import logging
import uuid
from typing import TYPE_CHECKING, Any

from jupyter_mcp_executor.notebook_adapter import NotebookAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.ydoc_helpers import (
    YDOC_AVAILABLE,
    YText,
    get_cell_index_by_id_ydoc,
)

if TYPE_CHECKING:
    from jupyter_mcp_executor.ydoc_helpers import NotebookRoom

logger = logging.getLogger(__name__)


class YDocAdapter(NotebookAdapter):
    """Adapter for notebooks open in JupyterLab (collaborative editing via YDoc).

    This adapter modifies the in-memory YDoc representation, which provides
    real-time collaboration and immediate visibility in JupyterLab.
    """

    def __init__(
        self, ydoc: Any, notebook_path: str, room: "NotebookRoom | None" = None
    ):
        """Initialize with YNotebook instance.

        Args:
            ydoc: YNotebook instance from jupyter_ydoc
            notebook_path: Normalized path to notebook
            room: The room serving ``ydoc``. When given, every operation first
                checks the room is still the one the server uses and keeps it
                alive; a replaced room is followed (see ``generation``).
        """
        if not YDOC_AVAILABLE:
            msg = "YDoc is not available (jupyter_ydoc not installed)"
            raise RuntimeError(msg)

        self.ydoc = ydoc
        self._notebook_path = notebook_path
        self._room = room
        self._generation = 0

    @property
    def generation(self) -> int:
        """How often the adapter moved to a recreated room (0 = never).

        The previous room's last edits may be missing from the new one: a
        deleted room cancels its pending save, and the new room loads the file.
        """
        return self._generation

    def keep_alive(self) -> None:
        """Cancel the room's pending cleanup, if it is still the live room."""
        if self._room is not None and self._room.is_attached():
            self._room.keep_alive()

    async def _live(self, *, write: bool = False) -> None:
        """Make ``self.ydoc`` the live document before an operation.

        A call can outlive its room (a long cell after the browser left): a
        stopped room's document is detached, and edits to it are lost without
        an error. Follow the notebook to its current room instead. Writes
        also keep that room alive: a deleted room cancels its pending save,
        so the write could otherwise miss the disk a recreated room loads.
        Reads do not, so merely viewing a notebook never pins its room.

        Args:
            write: The operation edits the document

        Raises:
            RuntimeError: If the room is gone and cannot be recreated
        """
        room = self._room
        if room is None:
            return
        if not room.is_attached():
            replacement = await room.reattach()
            if replacement is None:
                msg = (
                    f"The live document of {self._notebook_path} was closed "
                    "and could not be reopened; retry the call"
                )
                raise RuntimeError(msg)
            logger.info(f"YDoc room of {self._notebook_path} was replaced; following")
            self._room = room = replacement
            self.ydoc = replacement.ydoc
            self._generation += 1
        if write:
            room.keep_alive()

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
        await self._live()
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        # The cell as jupyter_ydoc saves it: plain Python objects, numbers
        # from Yjs (floats) back to ints, no transient execution_state.
        cell = self.ydoc.get_cell(position)
        # get_cell() drops the ids of nbformat <= 4.4 notebooks, as saving
        # does; the tools need them to find the cell again.
        cell_id = self.ydoc.ycells[position].get("id", "")

        return CellContent(
            source=str(cell.get("source", "")),
            cell_type=str(cell.get("cell_type", "code")),
            metadata=dict(cell.get("metadata", {})),
            execution_count=cell.get("execution_count"),
            outputs=list(cell.get("outputs", [])),
            cell_id=str(cell_id),
            attachments=dict(cell.get("attachments") or {}),
        )

    async def add_cell(
        self, cell_content: CellContent, position: int | None = None
    ) -> tuple[int, str]:
        """Add cell to YDoc."""
        await self._live(write=True)
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
        return position, cell_id

    async def edit_cell(self, position: int, content: CellContent) -> None:
        """Edit cell in YDoc.

        For code cells the previous outputs and execution_count are replaced
        by the ones in `content` (typically empty), so stale outputs from the
        old source never linger on the edited cell.
        """
        await self._live(write=True)
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
            ysource += source_str
        else:
            # Fall back to direct assignment
            ycell["source"] = YText(source_str) if YText else source_str

        # Update metadata — must be a pycrdt Map, not a plain dict
        if content.metadata:
            from pycrdt import Map  # noqa: PLC0415

            ycell["metadata"] = Map(content.metadata)

        # Reset execution state for code cells so outputs from the previous
        # source don't linger on the edited cell
        if content.cell_type == "code" and ycell.get("cell_type") == "code":
            await self.update_cell_outputs(position, None, list(content.outputs or []))
            ycell["execution_count"] = content.execution_count

        logger.info(f"Edited {content.cell_type} cell via YDoc at position {position}")

    async def delete_cell(self, position: int) -> None:
        """Delete cell from YDoc."""
        await self._live(write=True)
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        self.ydoc.ycells.pop(position)
        logger.info(f"Deleted cell via YDoc at position {position}")

    @staticmethod
    def _make_output_map(output: dict[str, Any]) -> Any:
        """Wrap one output dict as a pycrdt Map (stream text as Text).

        Outputs must be stored as pycrdt Map (with stream text as Text), not
        plain dicts — a raw dict breaks YNotebook.get_cell() → to_py() on save.
        """
        from pycrdt import Map, Text  # noqa: PLC0415

        out = dict(output)
        if out.get("output_type") == "stream":
            text = out.get("text", "")
            out["text"] = Text("".join(text) if isinstance(text, list) else text)
        return Map(out)

    async def update_cell_outputs(
        self,
        position: int,
        execution_count: int | None,
        outputs: list[dict[str, Any]] | None,
    ) -> None:
        """Update cell outputs in YDoc."""
        await self._live(write=True)
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        ycell = self.ydoc.ycells[position]

        # One transaction: browsers would otherwise render the cleared output
        # area before the new outputs (a flash per redrawn progress frame).
        with self.ydoc.ydoc.transaction():
            if execution_count is not None:
                ycell["execution_count"] = execution_count

            if outputs is not None:
                from pycrdt import Array  # noqa: PLC0415

                output_maps = [self._make_output_map(output) for output in outputs]

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

    async def append_cell_output(self, position: int, output: dict[str, Any]) -> None:
        """Append one output to a cell in YDoc (live streaming during execution).

        Consecutive stream outputs with the same name are coalesced by
        appending to the previous entry's in-place Text — a chatty loop
        printing thousands of lines stays one outputs entry with small CRDT
        deltas instead of thousands of Array inserts.
        """
        await self._live(write=True)
        if position < 0 or position >= self.ydoc.cell_number:
            msg = f"Cell index {position} out of range (notebook has {self.ydoc.cell_number} cells)"
            raise IndexError(msg)

        from pycrdt import Array, Text  # noqa: PLC0415

        ycell = self.ydoc.ycells[position]
        youtputs = ycell.get("outputs")
        if not isinstance(youtputs, Array):
            ycell["outputs"] = Array([])
            youtputs = ycell["outputs"]

        if output.get("output_type") == "stream" and len(youtputs) > 0:
            text = output.get("text", "")
            text_str = "".join(text) if isinstance(text, list) else text
            last = youtputs[len(youtputs) - 1]
            try:
                ytext = (
                    last.get("text") if last.get("output_type") == "stream" else None
                )
                if isinstance(ytext, Text) and last.get("name") == output.get(
                    "name", "stdout"
                ):
                    ytext += text_str
                    return
            except Exception:  # noqa: BLE001 - fall back to a new output entry
                logger.debug("Stream coalescing failed; appending as new output")

        youtputs.append(self._make_output_map(output))

    async def get_all_cells(self) -> list[CellContent]:
        """Get all cells from YDoc."""
        await self._live()
        cells = []
        for i in range(self.ydoc.cell_number):
            cells.append(await self.read_cell(i))
        return cells

    async def get_cell_count(self) -> int:
        """Get cell count from YDoc."""
        await self._live()
        return self.ydoc.cell_number

    async def find_cell_by_id(self, cell_id: str) -> tuple[CellContent, int] | None:
        """Find cell by UUID in YDoc."""
        await self._live()
        position = get_cell_index_by_id_ydoc(self.ydoc, cell_id)
        if position is None:
            return None

        cell = await self.read_cell(position)
        return (cell, position)

    async def find_cell_by_execution_count(
        self, execution_count: int
    ) -> tuple[CellContent, int] | None:
        """Find code cell by execution count in YDoc."""
        await self._live()
        for i in range(self.ydoc.cell_number):
            ycell = self.ydoc.ycells[i]
            if (
                ycell.get("cell_type") == "code"
                and ycell.get("execution_count") == execution_count
            ):
                cell = await self.read_cell(i)
                return (cell, i)

        return None
