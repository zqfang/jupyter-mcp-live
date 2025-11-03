"""Data models for notebook operations."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CellIdentifier:
    """Identifies a cell by one of three methods.

    Exactly one of the three identifiers must be provided.
    """
    execution_count: int | None = None
    position_index: int | None = None
    cell_id: str | None = None

    def __post_init__(self):
        """Validate that exactly one identifier is provided."""
        identifiers = [
            self.execution_count is not None,
            self.position_index is not None,
            self.cell_id is not None
        ]
        if sum(identifiers) != 1:
            raise ValueError(
                "Provide exactly ONE of: execution_count, position_index, or cell_id"
            )

    @property
    def identifier_type(self) -> str:
        """Return which identifier type is being used."""
        if self.execution_count is not None:
            return "execution_count"
        if self.position_index is not None:
            return "position_index"
        return "cell_id"


@dataclass
class KernelInfo:
    """Information about a running kernel."""
    id: str
    name: str
    last_activity: str | None
    execution_state: str
    connections: int


@dataclass
class ExecutionResult:
    """Result of code execution."""
    execution_count: int | None
    outputs: list[dict[str, Any]]
    status: str
    kernel_id: str
    started_new_kernel: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for MCP response."""
        result = {
            "execution_count": self.execution_count,
            "outputs": self.outputs,
            "status": self.status,
            "kernel_id": self.kernel_id,
        }
        if self.started_new_kernel:
            result["started_new_kernel"] = True
        if self.error:
            result["error"] = self.error
        return result

    def to_filtered_dict(self) -> dict[str, Any]:
        """Convert to dict with filtered outputs for MCP response."""
        from jupyter_server_mcp.output_filter import OutputFilter
        result = self.to_dict()
        result["outputs"] = OutputFilter.filter_outputs(self.outputs)
        return result


@dataclass
class CellContent:
    """Content and metadata for a notebook cell."""
    source: str | list[str]
    cell_type: str  # "code" or "markdown"
    metadata: dict[str, Any] = field(default_factory=dict)
    execution_count: int | None = None
    outputs: list[dict[str, Any]] = field(default_factory=list)
    cell_id: str | None = None

    def get_source_as_string(self) -> str:
        """Get source as a single string."""
        if isinstance(self.source, str):
            return self.source
        return "".join(self.source)


@dataclass
class NotebookInfo:
    """Information about a notebook."""
    path: str
    kernel_name: str
    kernel_id: str | None
    cell_count: int
    is_open: bool  # Whether notebook is open in JupyterLab (YDoc available)
