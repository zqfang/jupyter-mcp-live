"""Path normalization utilities."""

from pathlib import Path
from urllib.parse import unquote


def normalize_path(path: str) -> str:
    """Normalize a notebook path to an absolute, resolved path.

    Handles URL-encoded paths and resolves relative paths against the Jupyter
    server root directory.

    The resolved path (symlinks followed) must lie inside the root directory:
    Jupyter's contents manager cannot open anything else, so such a notebook
    could have no kernel session and no live document, and edits would go to
    a file JupyterLab never shows. This also blocks traversal such as
    "../../etc/x.ipynb". Only `.ipynb` files are accepted, so the notebook
    API cannot read or write other files. Without a server context (library
    use, tests) there is no root to check against.

    Args:
        path: File path (possibly URL-encoded or relative)

    Returns:
        Absolute normalized path

    Raises:
        ValueError: If path is empty, not a .ipynb file, or outside the
            server root
    """
    if not path or not path.strip():
        msg = "path cannot be empty"
        raise ValueError(msg)

    decoded_path = Path(unquote(path))

    # Deferred import to avoid circular dependencies at module load time
    from jupyter_mcp_executor.server_context import get_server_context  # noqa: PLC0415

    serverapp = get_server_context()
    root_dir: Path | None = None
    if serverapp and hasattr(serverapp, "root_dir"):
        root_dir = Path(serverapp.root_dir).resolve()

    if root_dir is None:
        resolved = decoded_path.resolve()
    else:
        resolved = (root_dir / decoded_path).resolve()  # absolute paths win
        if not resolved.is_relative_to(root_dir):
            msg = (
                f"{path} is outside the Jupyter root directory ({root_dir}); "
                "only notebooks inside it can be used"
            )
            raise ValueError(msg)

    # Only .ipynb files are allowed through the notebook API
    if resolved.suffix != ".ipynb":
        msg = "Only .ipynb files are permitted"
        raise ValueError(msg)

    return str(resolved)


def to_api_path(absolute_path: str) -> str:
    """Convert an absolute filesystem path to a Jupyter API path.

    Jupyter Server's SessionManager expects paths to be relative to the
    server's root_dir (e.g., "notebook.ipynb" or "subdir/notebook.ipynb")
    rather than absolute filesystem paths. This function converts absolute
    paths to the relative API format expected by JupyterLab.

    A path outside root_dir is returned as is; normalize_path() rejects
    those, so this happens only for unnormalized input.

    Args:
        absolute_path: Absolute filesystem path

    Returns:
        Path relative to server root_dir (if inside root_dir), using forward slashes.
        The path unchanged if it is outside root_dir.

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
        # Compare resolved paths: normalize_path() resolves symlinks, while
        # Jupyter keeps root_dir as configured (it may go through a symlink,
        # e.g. macOS /tmp or a symlinked home).
        root_dir = Path(serverapp.root_dir).resolve()
        resolved = abs_path.resolve()
        if resolved.is_relative_to(root_dir):
            # Forward slashes for API paths (Jupyter convention)
            return resolved.relative_to(root_dir).as_posix()

    # Outside root_dir (or no server): no API path exists; return it as is
    return str(abs_path)
