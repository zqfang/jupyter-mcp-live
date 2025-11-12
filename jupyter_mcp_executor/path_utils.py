"""Path normalization utilities."""

from pathlib import Path
from urllib.parse import unquote


def normalize_path(path: str) -> str:
    """Normalize a file path to an absolute path.

    Handles URL-encoded paths and resolves relative paths against
    the Jupyter server root directory if available.

    Args:
        path: File path (possibly URL-encoded or relative)

    Returns:
        Absolute normalized path

    Raises:
        ValueError: If path is empty
    """
    if not path or not path.strip():
        msg = "path cannot be empty"
        raise ValueError(msg)

    # URL decode in case path contains encoded characters
    decoded_path = unquote(path)

    # If already absolute, return normalized
    if Path(decoded_path).is_absolute():
        return str(Path(decoded_path).resolve())

    # Try to resolve relative to Jupyter server root
    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    if serverapp and hasattr(serverapp, 'root_dir'):
        root_dir = serverapp.root_dir
        return str((Path(root_dir) / decoded_path).resolve())

    # Fall back to current working directory
    return str(Path(decoded_path).resolve())


def to_api_path(absolute_path: str) -> str:
    """Convert an absolute filesystem path to a Jupyter API path.

    Jupyter Server's SessionManager expects paths to be relative to the
    server's root_dir (e.g., "notebook.ipynb" or "subdir/notebook.ipynb")
    rather than absolute filesystem paths. This function converts absolute
    paths to the relative API format expected by JupyterLab.

    Args:
        absolute_path: Absolute filesystem path

    Returns:
        Path relative to server root_dir, using forward slashes.
        If the path is outside root_dir or no server context is available,
        returns just the filename.

    Examples:
        # If root_dir = "/Users/fangzq/Github/jupyter-server-mcp"
        >>> to_api_path("/Users/fangzq/Github/jupyter-server-mcp/notebook.ipynb")
        "notebook.ipynb"

        >>> to_api_path("/Users/fangzq/Github/jupyter-server-mcp/data/analysis.ipynb")
        "data/analysis.ipynb"
    """
    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    abs_path = Path(absolute_path)

    serverapp = get_server_context()
    if serverapp and hasattr(serverapp, 'root_dir'):
        root_dir = Path(serverapp.root_dir)

        try:
            # If path is under root_dir, make it relative
            if abs_path.is_relative_to(root_dir):
                relative = abs_path.relative_to(root_dir)
                # Use forward slashes for API paths (Jupyter convention)
                return str(relative).replace('\\', '/')
        except (ValueError, AttributeError):
            # is_relative_to not available in Python 3.8, or path not relative
            pass

    # Fallback: if path is outside root_dir or no server context,
    # return just the filename (safest option)
    return abs_path.name
