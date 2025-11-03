"""Path normalization utilities."""

import os
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
        raise ValueError("path cannot be empty")

    # URL decode in case path contains encoded characters
    decoded_path = unquote(path)

    # If already absolute, return normalized
    if os.path.isabs(decoded_path):
        return os.path.abspath(decoded_path)

    # Try to resolve relative to Jupyter server root
    from jupyter_server_mcp.server_context import get_server_context

    serverapp = get_server_context()
    if serverapp and hasattr(serverapp, 'root_dir'):
        root_dir = serverapp.root_dir
        return os.path.abspath(os.path.join(root_dir, decoded_path))

    # Fall back to current working directory
    return os.path.abspath(decoded_path)
