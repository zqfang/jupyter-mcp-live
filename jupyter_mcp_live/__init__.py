"""Jupyter Server MCP Extension with configurable tools."""

from typing import Any

from .extension import MCPExtensionApp

__version__ = "0.6.2"


def _jupyter_server_extension_points() -> list[dict[str, Any]]:
    # pragma: no cover
    return [
        {
            "module": "jupyter_mcp_live.extension",
            "app": MCPExtensionApp,
        },
    ]


__all__ = ["MCPExtensionApp"]
