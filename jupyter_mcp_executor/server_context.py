"""Server context management for Jupyter ServerApp."""

from typing import Any

# Global context to store the Jupyter ServerApp instance
# This will be set when the MCP extension is initialized (see extension.py)
_server_context: dict[str, Any] = {"serverapp": None}


def set_server_context(serverapp: Any) -> None:
    """Set the global Jupyter ServerApp context.

    This is called by the MCP extension on initialization.

    Args:
        serverapp: Jupyter ServerApp instance
    """
    _server_context["serverapp"] = serverapp


def get_server_context() -> Any:
    """Get the global Jupyter ServerApp context.

    Returns:
        ServerApp instance or None if not initialized
    """
    return _server_context.get("serverapp")


def require_server_context() -> Any:
    """Get the server context, raising an error if not available.

    Returns:
        ServerApp instance

    Raises:
        RuntimeError: If server context is not initialized
    """
    serverapp = get_server_context()
    if serverapp is None:
        msg = (
            "Jupyter server context not available. "
            "Make sure the MCP extension is properly initialized."
        )
        raise RuntimeError(
            msg
        )
    return serverapp
