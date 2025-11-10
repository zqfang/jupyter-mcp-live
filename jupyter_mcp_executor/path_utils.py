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
