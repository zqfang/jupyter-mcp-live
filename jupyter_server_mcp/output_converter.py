"""Output format conversion utilities."""

from typing import Any
from nbformat.notebooknode import from_dict


def convert_outputs_to_notebook_nodes(outputs: list[dict]) -> list[Any]:
    """Convert output dicts to nbformat NotebookNode objects.

    Args:
        outputs: List of output dicts from kernel execution

    Returns:
        List of NotebookNode objects suitable for notebook cells
    """
    return [from_dict(output) for output in outputs]


def convert_notebook_nodes_to_dicts(outputs: list[Any]) -> list[dict[str, Any]]:
    """Convert nbformat NotebookNode objects to plain dicts.

    Args:
        outputs: List of NotebookNode output objects

    Returns:
        List of plain dicts
    """
    return [dict(output) for output in outputs]
