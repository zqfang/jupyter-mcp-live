"""Kernel management service for Jupyter notebooks."""

import os
import logging
from typing import Any
import nbformat
from nbformat.v4 import new_notebook

from jupyter_server_mcp.kernel_tracker import get_kernel_tracker
from jupyter_server_mcp.server_context import get_server_context, require_server_context
from jupyter_server_mcp.path_utils import normalize_path
from jupyter_server_mcp.notebook_io import write_notebook
from jupyter_server_mcp.kernel_utils import resolve_kernel_name, get_kernel_info

logger = logging.getLogger(__name__)


class KernelManager:
    """Service for managing Jupyter kernel lifecycle.

    Handles:
    - Listing available and running kernels
    - Starting kernels for notebooks
    - Switching kernels
    - Shutting down kernels
    - Notebook setup and creation
    """

    def __init__(self):
        """Initialize kernel manager."""
        self.kernel_tracker = get_kernel_tracker()

    async def list_running_kernels(self) -> dict[str, Any]:
        """List all running Jupyter kernels.

        Returns:
            Dict with "kernels" key containing list of kernel info dicts
        """
        serverapp = require_server_context()
        kernel_manager = serverapp.kernel_manager

        # Get all kernel IDs
        kernel_ids = list(kernel_manager.list_kernel_ids())

        kernels = []
        for kernel_id in kernel_ids:
            try:
                kernel = kernel_manager.get_kernel(kernel_id)

                kernel_info = {
                    "id": kernel_id,
                    "name": kernel.kernel_name if hasattr(kernel, "kernel_name") else "unknown",
                    "last_activity": (
                        kernel.last_activity.isoformat()
                        if hasattr(kernel, "last_activity") and kernel.last_activity
                        else None
                    ),
                    "execution_state": (
                        kernel.execution_state
                        if hasattr(kernel, "execution_state")
                        else "unknown"
                    ),
                    "connections": (
                        kernel.connections if hasattr(kernel, "connections") else 0
                    ),
                }

                kernels.append(kernel_info)

            except Exception as e:
                logger.warning(f"Could not get info for kernel {kernel_id}: {e}")
                kernels.append({
                    "id": kernel_id,
                    "name": "unknown",
                    "error": str(e)
                })

        return {"kernels": kernels}

    async def list_available_kernels(self) -> dict[str, Any]:
        """List all available kernel specifications.

        Returns:
            Dict with "kernelspecs" key containing kernel spec dicts
        """
        serverapp = require_server_context()
        kernel_spec_manager = serverapp.kernel_spec_manager

        # Get all kernel specs
        specs = kernel_spec_manager.get_all_specs()

        return {"kernelspecs": specs}

    async def setup_notebook(
        self,
        notebook_path: str,
        kernel_name: str = "python3"
    ) -> dict[str, Any]:
        """Setup a notebook (create if needed) and start kernel.

        Args:
            notebook_path: Path to notebook
            kernel_name: Name of kernel to use

        Returns:
            Dict with notebook info and kernel details
        """
        notebook_path = normalize_path(notebook_path)

        # Check if notebook exists
        notebook_exists = os.path.exists(notebook_path)

        # Get available kernels and resolve kernel name
        available_kernels = await self.list_available_kernels()
        kernelspecs = available_kernels.get("kernelspecs", {})

        # Resolve kernel name (handles exact match, language match, and custom kernels)
        kernel_name = resolve_kernel_name(kernel_name, kernelspecs, fallback="python3")

        if not notebook_exists:
            # Create new empty notebook
            logger.info(f"Creating new notebook: {notebook_path}")

            # Ensure directory exists
            dir_path = os.path.dirname(notebook_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            # Determine language info
            language_name, display_name = self._get_kernel_display_info(
                kernel_name, kernelspecs
            )

            # Create empty notebook
            nb = new_notebook(
                cells=[],
                metadata={
                    "kernelspec": {
                        "name": kernel_name,
                        "display_name": display_name,
                        "language": language_name
                    },
                    "language_info": {
                        "name": language_name
                    }
                }
            )

            write_notebook(notebook_path, nb)
            message = f"Notebook {notebook_path} created with kernel {kernel_name}"
        else:
            logger.info(f"Notebook {notebook_path} already exists")
            message = f"Notebook {notebook_path} already exists"

        # Start a kernel for this notebook
        kernel_id = await self._start_kernel_for_notebook(notebook_path, kernel_name)

        # Return notebook info
        return {
            "path": notebook_path,
            "name": os.path.basename(notebook_path),
            "message": message,
            "exists": notebook_exists,
            "kernel_name": kernel_name,
            "kernel_id": kernel_id,
        }

    async def switch_kernel(
        self,
        notebook_path: str,
        kernel_name: str
    ) -> dict[str, Any]:
        """Switch notebook to use a different kernel.

        Args:
            notebook_path: Path to notebook
            kernel_name: New kernel name

        Returns:
            Dict with status and new kernel info
        """
        notebook_path = normalize_path(notebook_path)

        # Shutdown old kernel if tracked
        old_kernel_id = self.kernel_tracker.get(notebook_path)
        if old_kernel_id:
            await self._shutdown_kernel(old_kernel_id)
            self.kernel_tracker.untrack(notebook_path)

        # Start new kernel
        new_kernel_id = await self._start_kernel_for_notebook(notebook_path, kernel_name)

        # Update notebook metadata
        self._update_notebook_kernel_metadata(notebook_path, kernel_name)

        return {
            "message": f"Kernel switched to {kernel_name}",
            "kernel_name": kernel_name,
            "kernel_id": new_kernel_id,
            "previous_kernel_id": old_kernel_id
        }

    async def shutdown_notebook(
        self,
        notebook_path: str,
        shutdown_kernel: bool = True
    ) -> dict[str, Any]:
        """Shutdown notebook session and optionally its kernel.

        Args:
            notebook_path: Path to notebook
            shutdown_kernel: Whether to shutdown the kernel

        Returns:
            Dict with status message
        """
        notebook_path = normalize_path(notebook_path)

        kernel_id = self.kernel_tracker.get(notebook_path)

        if kernel_id and shutdown_kernel:
            await self._shutdown_kernel(kernel_id)
            self.kernel_tracker.untrack(notebook_path)
            return {
                "message": "Notebook session closed and kernel shut down",
                "kernel_id": kernel_id
            }
        elif kernel_id:
            self.kernel_tracker.untrack(notebook_path)
            return {
                "message": "Notebook session closed (kernel still running)",
                "kernel_id": kernel_id
            }
        else:
            return {
                "message": "Notebook session closed (no kernel was tracked)",
                "kernel_id": None
            }

    async def _start_kernel_for_notebook(
        self,
        notebook_path: str,
        kernel_name: str
    ) -> str | None:
        """Start a kernel and track it for a notebook.

        Args:
            notebook_path: Path to notebook
            kernel_name: Kernel name

        Returns:
            Kernel ID or None if failed
        """
        serverapp = get_server_context()
        if not serverapp:
            logger.warning("No server context available, cannot start kernel")
            return None

        try:
            kernel_manager = serverapp.kernel_manager
            kernel_id = await kernel_manager.start_kernel(kernel_name=kernel_name)
            self.kernel_tracker.track(notebook_path, kernel_id)
            logger.info(
                f"Started kernel {kernel_name} (ID: {kernel_id}) for notebook {notebook_path}"
            )
            return kernel_id
        except Exception as e:
            logger.warning(f"Could not start kernel: {e}")
            return None

    async def _shutdown_kernel(self, kernel_id: str) -> None:
        """Shutdown a kernel.

        Args:
            kernel_id: Kernel ID to shutdown
        """
        serverapp = get_server_context()
        if not serverapp:
            return

        try:
            kernel_manager = serverapp.kernel_manager
            await kernel_manager.shutdown_kernel(kernel_id)
            logger.info(f"Shut down kernel {kernel_id}")
        except Exception as e:
            logger.warning(f"Error shutting down kernel {kernel_id}: {e}")

    def _get_kernel_display_info(
        self,
        kernel_name: str,
        kernelspecs: dict[str, Any]
    ) -> tuple[str, str]:
        """Get language name and display name for a kernel.

        Args:
            kernel_name: Kernel name
            kernelspecs: Available kernel specs

        Returns:
            Tuple of (language_name, display_name)
        """
        # Use kernel_utils for consistent detection
        info = get_kernel_info(kernel_name, kernelspecs)
        return (info["language"], info["display_name"])

    def _update_notebook_kernel_metadata(
        self,
        notebook_path: str,
        kernel_name: str
    ) -> None:
        """Update notebook's kernel metadata.

        Args:
            notebook_path: Path to notebook
            kernel_name: New kernel name
        """
        try:
            nb = nbformat.read(notebook_path, as_version=4)

            # Get kernel info
            available_kernels_result = None
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                available_kernels_result = loop.run_until_complete(
                    self.list_available_kernels()
                )
            except:
                pass

            kernelspecs = (
                available_kernels_result.get("kernelspecs", {})
                if available_kernels_result
                else {}
            )

            language_name, display_name = self._get_kernel_display_info(
                kernel_name, kernelspecs
            )

            # Update metadata
            nb.metadata["kernelspec"] = {
                "name": kernel_name,
                "display_name": display_name,
                "language": language_name
            }

            write_notebook(notebook_path, nb)
            logger.info(f"Updated kernel metadata in {notebook_path}")

        except Exception as e:
            logger.warning(f"Could not update notebook kernel metadata: {e}")
