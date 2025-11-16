"""Kernel management service for Jupyter notebooks."""

import logging
from pathlib import Path
from typing import Any

import nbformat
from nbformat.v4 import new_notebook

from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.kernel_utils import get_kernel_info, resolve_kernel_name
from jupyter_mcp_executor.notebook_io import write_notebook
from jupyter_mcp_executor.notebook_models import (
    KernelSwitchResult,
    NotebookSetupResult,
    ShutdownResult,
)
from jupyter_mcp_executor.path_utils import normalize_path, to_api_path
from jupyter_mcp_executor.server_context import (
    get_server_context,
    require_server_context,
)

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

    async def list_notebook_kernels(self) -> dict[str, Any]:
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
        kernel_name: str = "python3",
        force_new_kernel: bool = False,
    ) -> NotebookSetupResult:
        """Setup a notebook (create if needed) and start or reuse kernel.

        This function supports collaborative usage by checking for existing kernel
        sessions first. If a kernel is already running for the notebook (e.g., from
        JupyterLab or another user), it will reuse that kernel instead of creating
        a new one, preserving kernel state and avoiding conflicts.

        Workflow:
        1. Check if notebook file exists
        2. Check if notebook has an existing kernel session (via list_sessions)
        3. If notebook doesn't exist: create new notebook and start new kernel
        4. If notebook exists with running kernel: reuse the existing kernel
        5. If notebook exists without kernel: start new kernel

        Args:
            notebook_path: Path to notebook
            kernel_name: Name of kernel to use (only used if starting new kernel)
            force_new_kernel: If True, always start a new kernel even if one exists

        Returns:
            NotebookSetupResult with notebook info and kernel details
        """
        notebook_path = normalize_path(notebook_path)

        # Step 1: Check if notebook file exists
        notebook_exists = Path(notebook_path).exists()
        logger.info(f"Notebook exists: {notebook_exists} - {notebook_path}")

        # Get available kernels and resolve kernel name
        available_kernels = await self.list_notebook_kernels()
        kernelspecs = available_kernels.get("kernelspecs", {})
        kernel_name = resolve_kernel_name(kernel_name, kernelspecs, fallback="python3")

        # Step 2: Check for existing kernel session (unless force_new_kernel is True)
        kernel_id = None
        reused_kernel = False
        existing_session = None

        if not force_new_kernel:
            existing_session = await self._find_session_for_notebook(notebook_path)

            if existing_session:
                kernel_info = existing_session.get("kernel", {})
                kernel_id = kernel_info.get("id")
                existing_kernel_name = kernel_info.get("name", kernel_name)

                # Verify kernel is still alive
                serverapp = get_server_context()
                if serverapp and kernel_id:
                    try:
                        # Verify kernel exists and is accessible
                        serverapp.kernel_manager.get_kernel(kernel_id)
                        # Track existing kernel
                        self.kernel_tracker.track(notebook_path, kernel_id)
                        reused_kernel = True
                        kernel_name = existing_kernel_name
                        logger.info(
                            f"Reusing existing kernel {kernel_id} ({kernel_name}) "
                            f"for {notebook_path}"
                        )
                    except KeyError:
                        # Kernel died, reset to start new one
                        logger.info(
                            f"Existing kernel {kernel_id} not found, will start new kernel"
                        )
                        kernel_id = None

        # Step 3: Handle notebook creation/setup based on existence and kernel state
        if not notebook_exists:
            # Step 3a: Create new notebook and start new kernel
            logger.info(f"Creating new notebook: {notebook_path}")

            # Ensure directory exists
            dir_path = Path(notebook_path).parent
            if dir_path and str(dir_path) != ".":
                dir_path.mkdir(parents=True, exist_ok=True)

            # Determine language info
            language_name, display_name = self._get_kernel_display_info(
                kernel_name, kernelspecs
            )

            # Create empty notebook in memory
            nb = new_notebook(
                cells=[],
                metadata={
                    "kernelspec": {
                        "name": kernel_name,
                        "display_name": display_name,
                        "language": language_name,
                    },
                    "language_info": {"name": language_name},
                },
            )

            write_notebook(notebook_path, nb)

            # Start new kernel for the new notebook
            if kernel_id is None:
                kernel_id = await self._start_kernel_for_notebook(
                    notebook_path, kernel_name
                )

            logger.info(f"Notebook {notebook_path} created with kernel {kernel_name}")
        else:
            # Step 3b/3c: Notebook exists
            if reused_kernel:
                # Step 3b: Reusing existing kernel
                logger.info(
                    f"Notebook {notebook_path} exists (reused existing kernel {kernel_name})"
                )
            else:
                # Step 3c: Start new kernel for existing notebook
                if kernel_id is None:
                    kernel_id = await self._start_kernel_for_notebook(
                        notebook_path, kernel_name
                    )
                logger.info(
                    f"Notebook {notebook_path} exists (started new kernel {kernel_name})"
                )

        # Return notebook info
        return NotebookSetupResult(
            path=notebook_path,
            exists=notebook_exists,
            kernel_name=kernel_name,
            kernel_id=kernel_id,
        )

    async def switch_kernel(
        self, notebook_path: str, kernel_name: str
    ) -> KernelSwitchResult:
        """Switch notebook to use a different kernel.

        Deletes the old session, shuts down the old kernel, and creates
        a new kernel with a new session.

        Args:
            notebook_path: Path to notebook
            kernel_name: New kernel name

        Returns:
            KernelSwitchResult with status and new kernel info
        """
        notebook_path = normalize_path(notebook_path)

        # Delete the old session first
        await self._delete_session_for_notebook(notebook_path)

        # Shutdown old kernel if tracked
        old_kernel_id = self.kernel_tracker.get(notebook_path)
        if old_kernel_id:
            await self._shutdown_kernel(old_kernel_id)
            self.kernel_tracker.untrack(notebook_path)

        # Start new kernel (this will also create a new session)
        new_kernel_id = await self._start_kernel_for_notebook(
            notebook_path, kernel_name
        )

        # Update notebook metadata
        self._update_notebook_kernel_metadata(notebook_path, kernel_name)

        return KernelSwitchResult(
            kernel_name=kernel_name,
            kernel_id=new_kernel_id,
            previous_kernel_id=old_kernel_id,
        )

    async def shutdown_notebook(
        self, notebook_path: str, shutdown_kernel: bool = True
    ) -> ShutdownResult:
        """Shutdown notebook session and optionally its kernel.

        Deletes the Jupyter session and optionally shuts down the kernel.
        This ensures proper cleanup and prevents orphaned sessions.

        Args:
            notebook_path: Path to notebook
            shutdown_kernel: Whether to shutdown the kernel

        Returns:
            ShutdownResult with status message
        """
        notebook_path = normalize_path(notebook_path)

        kernel_id = self.kernel_tracker.get(notebook_path)

        # Delete the Jupyter session for this notebook
        await self._delete_session_for_notebook(notebook_path)

        if kernel_id and shutdown_kernel:
            await self._shutdown_kernel(kernel_id)
            self.kernel_tracker.untrack(notebook_path)
            return ShutdownResult(
                message="Notebook session closed and kernel shut down",
                kernel_id=kernel_id,
            )
        if kernel_id:
            self.kernel_tracker.untrack(notebook_path)
            return ShutdownResult(
                message="Notebook session closed (kernel still running)",
                kernel_id=kernel_id,
            )
        return ShutdownResult(
            message="Notebook session closed (no kernel was tracked)",
            kernel_id=None,
        )

    async def _delete_session_for_notebook(self, notebook_path: str) -> None:
        """Delete the Jupyter session for a notebook.

        Args:
            notebook_path: Path to notebook (already normalized)
        """
        serverapp = get_server_context()
        if not serverapp:
            logger.debug("No server context available for deleting session")
            return

        try:
            # Find the session for this notebook
            session = await self._find_session_for_notebook(notebook_path)

            if session:
                session_id = session.get("id")
                session_manager = serverapp.session_manager

                # Delete the session
                await session_manager.delete_session(session_id)

                logger.info(
                    f"Deleted session {session_id} for notebook {notebook_path}"
                )
            else:
                logger.debug(f"No session found to delete for {notebook_path}")

        except Exception as e:
            logger.warning(
                f"Error deleting session for {notebook_path}: {e}", exc_info=True
            )

    async def _find_session_for_notebook(
        self, notebook_path: str
    ) -> dict[str, Any] | None:
        """Find existing session for a notebook.

        This checks Jupyter's session manager to see if the notebook
        already has an active session with a running kernel (e.g., from JupyterLab or
        another user). This enables collaborative usage where multiple clients can
        share the same kernel.

        Args:
            notebook_path: Path to notebook (already normalized)

        Returns:
            Session dict if found, None otherwise
        """
        serverapp = get_server_context()
        if not serverapp:
            logger.debug("No server context available for checking existing session")
            return None

        try:
            # List all active sessions from session manager
            session_manager = serverapp.session_manager
            sessions = []
            for session in await session_manager.list_sessions():
                sessions.append(dict(session))

            # Get the notebook filename for comparison
            notebook_name = Path(notebook_path).name

            logger.debug(f"Checking for existing session for: {notebook_path}")
            logger.debug(f"Found {len(sessions)} active sessions")

            # Find matching notebook session
            for session in sessions:
                session_path = session.get("path", "")
                session_name = session.get("name", "")
                kernel_info = session.get("kernel", {})
                kernel_id = kernel_info.get("id")

                logger.debug(
                    f"Checking session: path='{session_path}', "
                    f"name='{session_name}', kernel_id={kernel_id}"
                )

                # Try multiple path matching strategies:
                # 1. Exact match on full path
                # 2. Match on normalized paths
                # 3. Match on filename only
                # 4. Match using samefile (for symlinks/relative paths)

                path_match = False

                # Strategy 1: Exact match
                if session_path == notebook_path:
                    path_match = True
                    logger.debug("  -> Matched via exact path comparison")

                # Strategy 2: Both normalized
                elif normalize_path(session_path) == notebook_path:
                    path_match = True
                    logger.debug("  -> Matched via normalized path comparison")

                # Strategy 3: Filename match (when paths are relative/different)
                elif (
                    session_name == notebook_name
                    or Path(session_path).name == notebook_name
                ):
                    path_match = True
                    logger.debug("  -> Matched via filename comparison")

                # Strategy 4: samefile (handles symlinks, relative paths)
                else:
                    try:
                        if (
                            Path(session_path).exists()
                            and Path(notebook_path).exists()
                            and Path(session_path).samefile(Path(notebook_path))
                        ):
                            path_match = True
                            logger.debug("  -> Matched via samefile comparison")
                    except (OSError, ValueError):
                        # One of the paths doesn't exist or comparison failed
                        pass

                if path_match and kernel_id:
                    logger.info(
                        f"Found existing session for {notebook_path} "
                        f"with kernel {kernel_id} (matched on: {session_path})"
                    )
                    return session

            logger.debug(
                f"No existing session found for {notebook_path} "
                f"(checked {len(sessions)} sessions)"
            )

        except Exception as e:
            logger.warning(f"Error checking for existing session: {e}", exc_info=True)

        return None

    async def _start_kernel_for_notebook(
        self, notebook_path: str, kernel_name: str
    ) -> str | None:
        """Start a kernel and track it for a notebook.

        Creates a Jupyter session which automatically creates and links a kernel
        to the notebook path. This ensures JupyterLab can discover and reuse the kernel.

        This approach avoids race conditions by letting SessionManager handle kernel
        creation atomically.

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
            session_manager = serverapp.session_manager
            contents_manager = serverapp.contents_manager
            notebook_name = Path(notebook_path).name

            # Convert absolute path to API path (relative to root_dir)
            # JupyterLab expects relative paths like "notebook.ipynb" or "subdir/notebook.ipynb"
            api_path = to_api_path(notebook_path)

            logger.debug(
                f"Creating session with new kernel: path='{api_path}', "
                f"name='{notebook_name}', kernel_name='{kernel_name}', type='notebook'"
            )

            # Preflight: ensure contents manager can see the file as a notebook
            root_dir = Path(serverapp.root_dir).resolve()
            try:
                contents_manager.get(api_path, type="notebook")
            except Exception as e:
                logger.error(
                    f"Contents manager cannot resolve path '{api_path}' "
                    f"(root_dir='{root_dir}'). Error: {e}"
                )
                return None

            # Create session this lets SessionManager create the kernel
            # This avoids race conditions where we create a kernel but session manager
            # doesn't see it yet and creates another one
            session = await session_manager.create_session(
                path=api_path,
                name=notebook_name,
                type="notebook",
                kernel_name=kernel_name,  # SessionManager will create kernel with this name
            )

            kernel_id = session["kernel"]["id"]

            # Track kernel locally
            self.kernel_tracker.track(notebook_path, kernel_id)

            logger.info(
                f"✅ Session and kernel created successfully! "
                f"Session ID: {session['id']}, Path: {api_path}, "
                f"Kernel: {kernel_id} ({kernel_name})"
            )

            # Verify session was actually created
            sessions = await session_manager.list_sessions()
            session_exists = any(s["id"] == session["id"] for s in sessions)
            if not session_exists:
                logger.error(
                    f"Session {session['id']} not found in session list after creation!"
                )
            else:
                logger.debug(f"Session {session['id']} verified in session list")

            return kernel_id

        except Exception as e:
            logger.error(
                f"❌ Session/kernel creation FAILED for {notebook_path}! Error: {e}",
                exc_info=True,
            )
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
        self, kernel_name: str, kernelspecs: dict[str, Any]
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
        self, notebook_path: str, kernel_name: str
    ) -> None:
        """Update notebook's kernel metadata.

        Args:
            notebook_path: Path to notebook
            kernel_name: New kernel name
        """
        try:
            nb = nbformat.read(notebook_path, as_version=4)

            # Get kernel info
            # Prefer synchronous retrieval to avoid awaiting in a sync method
            serverapp = require_server_context()
            kernel_spec_manager = serverapp.kernel_spec_manager
            # Get all kernel specs
            kernelspecs = kernel_spec_manager.get_all_specs()

            language_name, display_name = self._get_kernel_display_info(
                kernel_name, kernelspecs
            )

            # Update metadata
            nb.metadata["kernelspec"] = {
                "name": kernel_name,
                "display_name": display_name,
                "language": language_name,
            }

            write_notebook(notebook_path, nb)
            logger.info(f"Updated kernel metadata in {notebook_path}")

        except Exception as e:
            logger.warning(f"Could not update notebook kernel metadata: {e}")
