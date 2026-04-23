"""Notebook file I/O operations."""

from typing import Any

import nbformat


class NotebookContext:
    """Context manager for reading/writing notebooks atomically.

    Usage:
        with NotebookContext(notebook_path) as nb:
            # Modify nb
            nb.cells.append(new_cell)
        # Changes are automatically saved on exit
    """

    def __init__(self, notebook_path: str):
        """Initialize context with notebook path.

        Args:
            notebook_path: Path to notebook file
        """
        self.notebook_path = notebook_path
        self.notebook: Any = None

    def __enter__(self) -> Any:
        """Read notebook on context entry.

        Returns:
            Notebook object
        """
        self.notebook = read_notebook(self.notebook_path)
        return self.notebook

    def __exit__(self, exc_type, _exc_val, _exc_tb):
        """Write notebook on context exit if no exception occurred.

        Args:
            exc_type: Exception type (if any)
            _exc_val: Exception value (if any)
            _exc_tb: Exception traceback (if any)
        """
        if exc_type is None and self.notebook is not None:
            write_notebook(self.notebook_path, self.notebook)


def read_notebook(notebook_path: str) -> Any:
    """Read a notebook from file.

    Args:
        notebook_path: Path to notebook file

    Returns:
        Notebook object
    """
    return nbformat.read(notebook_path, as_version=4)


def write_notebook(notebook_path: str, nb: Any) -> None:
    """Write a notebook to file.

    Args:
        notebook_path: Path to notebook file
        nb: Notebook object to write
    """
    nbformat.write(nb, notebook_path)
