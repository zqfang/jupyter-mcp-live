"""Path normalization utilities."""

from pathlib import Path
from urllib.parse import unquote


def normalize_path(path: str) -> str:
    """Normalize a file path to an absolute path.

    Handles URL-encoded paths and resolves relative paths against
    the Jupyter server root directory if available.

    Relative paths that resolve outside the server root are rejected to
    prevent path traversal attacks (e.g. "../../etc/passwd"). Absolute
    paths are trusted as-is because notebooks may legitimately reside
    outside the server root directory.

    Args:
        path: File path (possibly URL-encoded or relative)

    Returns:
        Absolute normalized path

    Raises:
        ValueError: If path is empty or a relative path escapes server root
    """
    if not path or not path.strip():
        msg = "path cannot be empty"
        raise ValueError(msg)

    decoded_path = unquote(path)

    # Deferred import to avoid circular dependencies at module load time
    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    root_dir: Path | None = None
    if serverapp and hasattr(serverapp, "root_dir"):
        root_dir = Path(serverapp.root_dir).resolve()

    if Path(decoded_path).is_absolute():
        resolved = Path(decoded_path).resolve()
    elif root_dir is not None:
        resolved = (root_dir / decoded_path).resolve()
        # Guard: relative paths must not escape the server root (traversal prevention)
        if not resolved.is_relative_to(root_dir):
            e = f"Path '{path}' resolves outside server root '{root_dir}'"
            raise ValueError(e)
    else:
        resolved = Path(decoded_path).resolve()

    return str(resolved)


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

    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

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
