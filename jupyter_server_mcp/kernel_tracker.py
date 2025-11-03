"""Kernel tracking for notebook sessions."""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class KernelTracker:
    """Track active kernels per notebook to support multi-kernel workflows.

    This allows each notebook to maintain its own kernel session across
    multiple operations.
    """

    def __init__(self):
        """Initialize kernel tracker."""
        self._kernels: Dict[str, str] = {}  # Maps notebook_path -> kernel_id

    def track(self, notebook_path: str, kernel_id: str) -> None:
        """Track a kernel for a notebook.

        Args:
            notebook_path: Normalized path to notebook
            kernel_id: Kernel ID to track
        """
        if kernel_id:
            self._kernels[notebook_path] = kernel_id
            logger.debug(f"Tracking kernel {kernel_id} for {notebook_path}")

    def get(self, notebook_path: str) -> str | None:
        """Get the tracked kernel ID for a notebook.

        Args:
            notebook_path: Normalized path to notebook

        Returns:
            Kernel ID if tracked, None otherwise
        """
        return self._kernels.get(notebook_path)

    def untrack(self, notebook_path: str) -> None:
        """Stop tracking kernel for a notebook.

        Args:
            notebook_path: Normalized path to notebook
        """
        if notebook_path in self._kernels:
            kernel_id = self._kernels[notebook_path]
            del self._kernels[notebook_path]
            logger.debug(f"Untracked kernel {kernel_id} for {notebook_path}")

    def get_all_tracked(self) -> Dict[str, str]:
        """Get all tracked kernels.

        Returns:
            Dict mapping notebook paths to kernel IDs
        """
        return self._kernels.copy()


# Global singleton instance
_kernel_tracker = KernelTracker()


def get_kernel_tracker() -> KernelTracker:
    """Get the global kernel tracker instance.

    Returns:
        KernelTracker singleton
    """
    return _kernel_tracker
