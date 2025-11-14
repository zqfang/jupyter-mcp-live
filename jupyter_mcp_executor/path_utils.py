"""Path normalization utilities."""

from pathlib import Path
from urllib.parse import unquote

from jupyter_mcp_executor.server_context import get_server_context


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
    if serverapp and hasattr(serverapp, "root_dir"):
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

    For paths outside root_dir, returns the absolute path to ensure correct
    session matching when notebooks are opened from anywhere on the filesystem.

    Args:
        absolute_path: Absolute filesystem path

    Returns:
        Path relative to server root_dir (if inside root_dir), using forward slashes.
        Absolute path (if outside root_dir) for correct session matching.

    Examples:
        # If root_dir = "/Users/fangzq/Github/jupyter-server-mcp"
        >>> to_api_path("/Users/fangzq/Github/jupyter-server-mcp/notebook.ipynb")
        "notebook.ipynb"

        >>> to_api_path("/Users/fangzq/Github/jupyter-server-mcp/data/analysis.ipynb")
        "data/analysis.ipynb"

        >>> to_api_path("/Users/fangzq/workspace/external.ipynb")
        "/Users/fangzq/workspace/external.ipynb"  # Outside root_dir, use absolute
    """

    abs_path = Path(absolute_path)

    serverapp = get_server_context()
    if serverapp and hasattr(serverapp, "root_dir"):
        root_dir = Path(serverapp.root_dir)

        try:
            # If path is under root_dir, make it relative
            if abs_path.is_relative_to(root_dir):
                relative = abs_path.relative_to(root_dir)
                # Use forward slashes for API paths (Jupyter convention)
                return str(relative).replace("\\", "/")
        except (ValueError, AttributeError):
            # is_relative_to not available in Python 3.8, or path not relative
            pass

    # For paths outside root_dir: use absolute path
    # This ensures session matching works correctly when notebooks are
    # outside the Jupyter server's root directory
    return str(abs_path)
