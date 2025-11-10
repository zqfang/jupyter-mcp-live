"""Validation utilities for notebook operations."""

from typing import Any


def validate_cell_index(nb: Any, index: int, operation: str = "access") -> None:
    """Validate that a cell index is within bounds.

    Args:
        nb: Notebook object with cells attribute
        index: Cell index to validate
        operation: Operation being performed (for error message)

    Raises:
        IndexError: If index is out of range
    """
    cell_count = len(nb.cells)
    if index < 0 or index >= cell_count:
        msg = (
            f"Cannot {operation} cell at index {index}. "
            f"Notebook has {cell_count} cells (valid indices: 0-{cell_count - 1})"
        )
        raise IndexError(
            msg
        )


def validate_code_cell(cell: Any, index: int) -> None:
    """Validate that a cell is a code cell.

    Args:
        cell: Cell object to validate
        index: Cell index (for error message)

    Raises:
        ValueError: If cell is not a code cell
    """
    if cell.cell_type != "code":
        msg = (
            f"Cell at index {index} is a {cell.cell_type} cell, not a code cell. "
            f"This operation requires a code cell."
        )
        raise ValueError(
            msg
        )


def validate_markdown_cell(cell: Any, index: int) -> None:
    """Validate that a cell is a markdown cell.

    Args:
        cell: Cell object to validate
        index: Cell index (for error message)

    Raises:
        ValueError: If cell is not a markdown cell
    """
    if cell.cell_type != "markdown":
        msg = (
            f"Cell at index {index} is a {cell.cell_type} cell, not a markdown cell. "
            f"This operation requires a markdown cell."
        )
        raise ValueError(
            msg
        )
