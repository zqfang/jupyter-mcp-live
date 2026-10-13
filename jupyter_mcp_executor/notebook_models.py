"""Data models for notebook operations."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
            self.cell_id is not None,
        ]
        if sum(identifiers) != 1:
            msg = "Provide exactly ONE of: execution_count, position_index, or cell_id"
            raise ValueError(msg)

    @property
    def identifier_type(self) -> str:
        """Return which identifier type is being used."""
        if self.execution_count is not None:
            return "execution_count"
        if self.position_index is not None:
            return "position_index"
        return "cell_id"


class ExecutionResult(BaseModel):
    """Result of code execution in a Jupyter kernel."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "execution_count": 1,
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": "Hello\n"}
                    ],
                    "status": "ok",
                    "kernel_id": "abc-123-def",
                    "started_new_kernel": False,
                }
            ]
        }
    )

    execution_count: int | None = Field(
        None, description="Execution count assigned by the kernel"
    )
    outputs: list[dict[str, Any]] = Field(
        default_factory=list, description="List of outputs from code execution"
    )
    status: Literal["ok", "error"] = Field(
        description="Execution status: 'ok' for success, 'error' for failure"
    )
    kernel_id: str = Field(description="ID of the kernel that executed the code")
    started_new_kernel: bool = Field(
        False, description="Whether a new kernel was started for this execution"
    )
    error: str | None = Field(None, description="Error message if status is 'error'")

    def to_filtered_dict(self) -> dict[str, Any]:
        """Convert to dict with filtered outputs for MCP response."""
        from jupyter_mcp_executor.output_filter import OutputFilter  # noqa: PLC0415

        result = self.model_dump(exclude_none=True)
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


class CellOperationResult(BaseModel):
    """Result of a cell operation (add, edit, delete).

    This is a lightweight result for non-execution operations.
    For code execution results, use ExecutionResult instead.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "Added markdown cell at position 0",
                    "position": 0,
                    "cell_type": "markdown",
                }
            ]
        }
    )

    message: str = Field(
        description="Human-readable description of the operation performed"
    )
    position: int = Field(
        ge=0, description="Zero-indexed position of the cell in the notebook"
    )
    cell_type: Literal["code", "markdown"] | None = Field(
        None,
        description="Type of cell: 'code' for executable code, 'markdown' for text",
    )


class NotebookSetupResult(BaseModel):
    """Result of setting up a notebook."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "path": "/path/to/notebook.ipynb",
                    "exists": True,
                    "kernel_name": "python3",
                    "kernel_id": "abc-123-def",
                }
            ]
        }
    )

    path: str = Field(description="Path to the notebook file")
    exists: bool = Field(
        description="Whether the notebook already existed before setup"
    )
    kernel_name: str = Field(description="Name of the kernel spec that was started")
    kernel_id: str = Field(description="ID of the running kernel")


class KernelSwitchResult(BaseModel):
    """Result of switching a notebook's kernel."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "kernel_name": "python3",
                    "kernel_id": "new-kernel-123",
                    "previous_kernel_id": "old-kernel-456",
                }
            ]
        }
    )

    kernel_name: str = Field(description="Name of the new kernel spec that was started")
    kernel_id: str = Field(description="ID of the new running kernel")
    previous_kernel_id: str | None = Field(
        None, description="ID of the previous kernel that was shut down"
    )


class ShutdownResult(BaseModel):
    """Result of shutting down a notebook's kernel."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"message": "Kernel shut down successfully", "kernel_id": "abc-123-def"}
            ]
        }
    )

    message: str = Field(
        description="Human-readable description of the shutdown result"
    )
    kernel_id: str | None = Field(
        None, description="ID of the kernel that was shut down"
    )


class KernelActionResult(BaseModel):
    """Result of interrupting or restarting a notebook's kernel."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "kernel_id": "abc-123-def",
                    "message": "Interrupt sent; variables are kept",
                }
            ]
        }
    )

    kernel_id: str = Field(description="ID of the kernel the action applied to")
    message: str = Field(description="What happened and what to do next")


class CellInfo(BaseModel):
    """Information about a notebook cell."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "cell_type": "code",
                    "source": "print('hello')",
                    "position": 0,
                    "execution_count": 1,
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": "hello\n"}
                    ],
                    "id": "abc-123-def",
                }
            ]
        }
    )

    cell_type: Literal["code", "markdown"] = Field(
        description="Type of cell: 'code' for executable code, 'markdown' for text"
    )
    source: str = Field(description="Source code or markdown content of the cell")
    position: int = Field(
        ge=0, description="Zero-indexed position of the cell in the notebook"
    )
    execution_count: int | None = Field(
        None, description="Execution count for code cells (None for markdown)"
    )
    outputs: list[dict[str, Any]] = Field(
        default_factory=list, description="List of outputs (for code cells only)"
    )
    id: str = Field(description="Unique identifier for the cell")
