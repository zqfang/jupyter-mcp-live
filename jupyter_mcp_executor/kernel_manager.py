"""Kernel management service for Jupyter notebooks."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from jupyter_core.utils import ensure_async
from nbformat.v4 import new_notebook

from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker
from jupyter_mcp_executor.kernel_utils import (
    default_kernel_name,
    get_kernel_info,
    is_kernel_alive,
    resolve_kernel_name,
)
from jupyter_mcp_executor.notebook_models import (
    KernelActionResult,
    KernelSwitchResult,
    NotebookSetupResult,
    ShutdownResult,
)
from jupyter_mcp_executor.notebook_store import (
    editing_notebook,
    ensure_directory,
    load_notebook,
    notebook_exists,
    save_notebook,
)
from jupyter_mcp_executor.path_utils import normalize_path, to_api_path
from jupyter_mcp_executor.server_context import (
    get_server_context,
    require_server_context,
)

logger = logging.getLogger(__name__)


class KernelStartError(RuntimeError):
    """A kernel (with its Jupyter session) could not be started for a notebook."""


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

        # get_all_specs scans kernelspec directories on disk — run it in a
        # worker thread so it never blocks the server's event loop
        specs = await asyncio.to_thread(kernel_spec_manager.get_all_specs)

        return {"kernelspecs": specs}

    async def describe_kernels(self) -> dict[str, Any]:
        """The kernels an agent can choose from, and the server's default.

        Only each kernelspec's display name and language: argv, env and
        resource_dir cost tokens and env may hold values (keys, tokens).
        """
        specs = (await self.list_notebook_kernels()).get("kernelspecs", {})
        kernels = {}
        for name, entry in specs.items():
            spec = entry.get("spec", {})
            kernels[name] = {
                "display_name": spec.get("display_name", name),
                "language": spec.get("language", ""),
            }
        return {
            "default_kernel": default_kernel_name(get_server_context()),
            "kernelspecs": kernels,
        }

    async def setup_notebook(
        self,
        notebook_path: str,
        kernel_name: str | None = None,
        force_new_kernel: bool = False,
    ) -> NotebookSetupResult:
        """Setup a notebook (create if needed) and start or reuse kernel.

        This function supports collaborative usage by checking for existing kernel
        sessions first. If a kernel is already running for the notebook (e.g., from
        JupyterLab or another user), it will reuse that kernel instead of creating
        a new one, preserving kernel state and avoiding conflicts.

        Workflow:
        1. Check if notebook file exists
        2. Check for an existing kernel: Jupyter session first (via
           list_sessions), then a locally tracked kernel without a session
        3. If notebook doesn't exist: create new notebook and start new kernel
        4. If notebook exists with running kernel: reuse the existing kernel
        5. If notebook exists without kernel: start new kernel

        Args:
            notebook_path: Path to notebook
            kernel_name: Name of kernel to use (only used if starting new
                kernel); None means the server's default kernel
            force_new_kernel: If True, always start a new kernel even if one exists

        Returns:
            NotebookSetupResult with notebook info and kernel details
        """
        notebook_path = normalize_path(notebook_path)
        if kernel_name is None:
            kernel_name = default_kernel_name(get_server_context())

        # Step 1: Check if notebook file exists
        exists = await notebook_exists(notebook_path)
        logger.info(f"Notebook exists: {exists} - {notebook_path}")

        kernelspecs = (await self.list_notebook_kernels()).get("kernelspecs", {})

        # Step 2: Reuse the notebook's kernel, or replace it (force_new_kernel)
        kernel_id, kernel_name = await self._choose_kernel(
            notebook_path, kernel_name, kernelspecs, force_new_kernel=force_new_kernel
        )
        reused = kernel_id is not None

        # Step 3: Create the notebook if needed, then start a kernel unless one
        # is reused
        if not exists:
            await self._create_notebook(notebook_path, kernel_name, kernelspecs)
        if not reused:
            kernel_id = await self._start_kernel_for_notebook(
                notebook_path, kernel_name
            )

        if not exists:
            logger.info(f"Notebook {notebook_path} created with kernel {kernel_name}")
        else:
            how = "reused existing" if reused else "started new"
            logger.info(f"Notebook {notebook_path} exists ({how} kernel {kernel_name})")

        return NotebookSetupResult(
            path=notebook_path,
            exists=exists,
            kernel_name=kernel_name,
            kernel_id=kernel_id,
        )

    async def _choose_kernel(
        self,
        notebook_path: str,
        kernel_name: str,
        kernelspecs: dict[str, Any],
        *,
        force_new_kernel: bool,
    ) -> tuple[str | None, str]:
        """Pick setup_notebook's kernel: an existing one, or a new one's name.

        Args:
            notebook_path: Path to notebook (already normalized)
            kernel_name: Requested kernel name (not yet resolved)
            kernelspecs: Available kernelspecs
            force_new_kernel: Stop the notebook's kernel instead of reusing it

        Returns:
            (kernel_id, kernel_name) of the kernel to reuse, or (None, the
            resolved requested name) when a new kernel must be started.

        Raises:
            ValueError: If a kernel must be started and kernel_name is unknown.
        """
        if force_new_kernel:
            await self._replace_notebook_kernel(notebook_path)
        else:
            reused = await self._find_reusable_kernel(notebook_path, kernel_name)
            if reused is not None:
                return reused

        # Resolve the requested kernel name strictly only when a kernel
        # will actually be started: an unknown kernel raises (listing the
        # available ones) instead of silently running code on python3.
        # Reusing an existing kernel never needs the name resolved.
        return None, resolve_kernel_name(kernel_name, kernelspecs)

    async def _replace_notebook_kernel(self, notebook_path: str) -> None:
        """Stop the notebook's session and kernel before force_new_kernel starts one.

        "Force new" means REPLACE, not ADD. jupyter_server's
        SessionManager.create_session does not deduplicate by path -- it just
        mints a new session id -- so starting a kernel without tearing the old
        session down leaves N sessions and N kernels on one notebook. The agent
        and JupyterLab then pick different ones (see _find_session_for_notebook),
        which breaks the guarantee that both drive the same kernel, and every
        abandoned kernel keeps its memory (GPU included) until the server stops.

        Args:
            notebook_path: Path to notebook (already normalized)
        """
        stale_id = await self._stop_notebook_kernel(notebook_path)
        if stale_id:
            logger.info(
                f"force_new_kernel: replaced kernel {stale_id} for {notebook_path}"
            )

    async def _find_reusable_kernel(
        self, notebook_path: str, kernel_name: str
    ) -> tuple[str, str] | None:
        """Find a live kernel the notebook already uses, and track it.

        Checks the notebook's Jupyter session first, then a locally tracked
        kernel without a session.

        Args:
            notebook_path: Path to notebook (already normalized)
            kernel_name: Name to report if the kernel's own is unknown

        Returns:
            (kernel_id, kernel_name) of the kernel to reuse, or None if a new
            kernel must be started.
        """
        serverapp = get_server_context()
        reused = await self._reuse_session_kernel(notebook_path, kernel_name, serverapp)
        if reused is None and serverapp:
            reused = self._reuse_tracked_kernel(notebook_path, kernel_name, serverapp)
        return reused

    async def _reuse_session_kernel(
        self, notebook_path: str, kernel_name: str, serverapp: Any
    ) -> tuple[str, str] | None:
        """Return the live kernel of the notebook's Jupyter session, if any.

        Args:
            notebook_path: Path to notebook (already normalized)
            kernel_name: Name to report if the session does not name its kernel
            serverapp: The Jupyter server app (may be None)

        Returns:
            (kernel_id, kernel_name), or None if there is no live session kernel.
        """
        existing_session = await self._find_session_for_notebook(notebook_path)
        if not existing_session:
            return None

        kernel_info = existing_session.get("kernel", {})
        kernel_id = kernel_info.get("id")

        # Verify kernel is still alive
        if not is_kernel_alive(serverapp, kernel_id):
            # Kernel died; the caller starts a new one
            logger.info(f"Existing kernel {kernel_id} not found, will start new kernel")
            return None

        self.kernel_tracker.track(notebook_path, kernel_id)
        existing_kernel_name = kernel_info.get("name", kernel_name)
        logger.info(
            f"Reusing existing kernel {kernel_id} ({existing_kernel_name}) "
            f"for {notebook_path}"
        )
        return kernel_id, existing_kernel_name

    def _reuse_tracked_kernel(
        self, notebook_path: str, kernel_name: str, serverapp: Any
    ) -> tuple[str, str] | None:
        """Return the notebook's tracked kernel if it is still alive.

        Fallback: a kernel may be tracked without a Jupyter session (e.g.,
        auto-started by an execute call). Reuse it instead of starting a new
        kernel and losing its state. A dead tracked kernel is untracked.

        Args:
            notebook_path: Path to notebook (already normalized)
            kernel_name: Name to report if the kernel does not name itself
            serverapp: The Jupyter server app

        Returns:
            (kernel_id, kernel_name), or None if no live kernel is tracked.
        """
        tracked_id = self.kernel_tracker.get(notebook_path)
        if not tracked_id:
            return None
        if not is_kernel_alive(serverapp, tracked_id):
            self.kernel_tracker.untrack(notebook_path)
            return None

        kernel = serverapp.kernel_manager.get_kernel(tracked_id)
        tracked_kernel_name = getattr(kernel, "kernel_name", kernel_name)
        logger.info(
            f"Reusing tracked kernel {tracked_id} ({tracked_kernel_name}) "
            f"for {notebook_path} (no session found)"
        )
        return tracked_id, tracked_kernel_name

    async def _create_notebook(
        self, notebook_path: str, kernel_name: str, kernelspecs: dict[str, Any]
    ) -> None:
        """Create an empty notebook for a kernel, then bring its YDoc room online.

        Args:
            notebook_path: Path to notebook (already normalized)
            kernel_name: Resolved kernel name, written as the kernelspec
            kernelspecs: Available kernelspecs, for display name and language
        """
        logger.info(f"Creating new notebook: {notebook_path}")

        await ensure_directory(str(Path(notebook_path).parent))

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

        await save_notebook(notebook_path, nb)
        await self._establish_ydoc_room(notebook_path)

    async def _establish_ydoc_room(self, notebook_path: str) -> None:
        """Bring a just-created notebook's YDoc room online.

        Create the notebook "through the YDoc": seed the empty file on disk
        first (so file_id_manager / file loaders can resolve it), then
        immediately bring the YDoc room online and initialize it from that
        baseline. Establishing ystore ownership at creation time — instead of
        lazily on the first cell edit — means every later edit is recorded as
        CRDT history in the ystore. Without this, the disk-only baseline lives
        outside the ystore and gets reconciled against replayed CRDT ops when
        the room is torn down and recreated, which materialized early cells
        twice (duplicated cells). If YDoc infrastructure is unavailable we keep
        the disk-only notebook, preserving the NbformatAdapter fallback.

        Args:
            notebook_path: Path to notebook (already normalized and on disk)
        """
        try:
            from jupyter_mcp_executor.ydoc_helpers import (  # noqa: PLC0415
                ensure_ynotebook,
            )

            ydoc = await ensure_ynotebook(notebook_path)
            if ydoc is not None:
                logger.info(f"YDoc room established at creation for {notebook_path}")
            else:
                logger.info(f"YDoc unavailable; {notebook_path} created on disk only")
        except Exception as e:  # noqa: BLE001  # pragma: no cover - defensive
            logger.warning(
                f"Could not establish YDoc room at creation for {notebook_path}: {e}"
            )

    async def get_kernel_for_notebook(
        self, notebook_path: str, kernel_name: str | None = None
    ) -> tuple[str | None, bool]:
        """Resolve a live kernel for a notebook, reusing before creating.

        Resolution order:
        1. Kernel already tracked for this notebook (if still alive)
        2. Kernel from an existing Jupyter session (e.g., notebook open in
           JupyterLab, or set up by another client)
        3. Start a new kernel with a proper Jupyter session, so it is
           discoverable by later setup_notebook/session lookups

        Args:
            notebook_path: Path to notebook
            kernel_name: Kernel to use only if a new kernel must be started;
                defaults to the notebook's kernelspec metadata, then the
                server's default kernel

        Returns:
            Tuple of (kernel_id, started_new). kernel_id is None only if no
            server context is available. started_new is True only when a
            brand-new kernel was started (no prior state preserved).

        Raises:
            KernelStartError: If a new kernel was needed but could not start.
        """
        notebook_path = normalize_path(notebook_path)
        serverapp = get_server_context()
        if not serverapp:
            return self.kernel_tracker.get(notebook_path), False

        # 1-2. Reuse the tracked kernel or an existing session's kernel
        existing_id = await self.find_kernel_for_notebook(notebook_path)
        if existing_id:
            return existing_id, False

        # 3. Start a new session-backed kernel.  The name comes from notebook
        # metadata here (not an explicit user request), so resolution stays
        # lenient: an alien kernelspec name falls back to the server's default
        # kernel with a warning instead of making every execution fail.
        default_name = default_kernel_name(serverapp)
        if kernel_name is None:
            kernel_name = (
                await self._read_notebook_kernel_name(notebook_path) or default_name
            )
        available_kernels = await self.list_notebook_kernels()
        kernel_name = resolve_kernel_name(
            kernel_name, available_kernels.get("kernelspecs", {}), fallback=default_name
        )
        kernel_id = await self._start_kernel_for_notebook(notebook_path, kernel_name)
        return kernel_id, True

    async def find_kernel_for_notebook(self, notebook_path: str) -> str | None:
        """Return the notebook's running kernel without ever starting one.

        Lookup order: the tracked kernel (if still alive), then the kernel of
        an existing Jupyter session (e.g. the notebook is open in JupyterLab),
        which then becomes the tracked kernel.

        Args:
            notebook_path: Path to notebook

        Returns:
            The kernel ID, or None if the notebook has no running kernel.
        """
        notebook_path = normalize_path(notebook_path)
        serverapp = get_server_context()
        if not serverapp:
            return self.kernel_tracker.get(notebook_path)

        # 1. Tracked kernel, verified alive
        tracked_id = self.kernel_tracker.get(notebook_path)
        if tracked_id:
            if is_kernel_alive(serverapp, tracked_id):
                return tracked_id
            logger.info(
                f"Tracked kernel {tracked_id} for {notebook_path} is gone; untracking"
            )
            self.kernel_tracker.untrack(notebook_path)

        # 2. Existing Jupyter session
        session = await self._find_session_for_notebook(notebook_path)
        if session:
            session_kernel_id = session.get("kernel", {}).get("id")
            if session_kernel_id:
                if is_kernel_alive(serverapp, session_kernel_id):
                    self.kernel_tracker.track(notebook_path, session_kernel_id)
                    logger.info(
                        f"Attached to existing session kernel {session_kernel_id} "
                        f"for {notebook_path}"
                    )
                    return session_kernel_id
                logger.info(
                    f"Session kernel {session_kernel_id} for {notebook_path} is gone"
                )

        return None

    async def _require_running_kernel(self, notebook_path: str) -> str:
        """Return the notebook's running kernel or raise with a recovery hint."""
        kernel_id = await self.find_kernel_for_notebook(notebook_path)
        if kernel_id is None:
            msg = (
                f"No running kernel for {notebook_path}; call setup_notebook "
                "first (or execute a cell, which starts one)"
            )
            raise ValueError(msg)
        return kernel_id

    async def interrupt_notebook_kernel(self, notebook_path: str) -> KernelActionResult:
        """Interrupt the notebook's running cell; kernel state is kept.

        Args:
            notebook_path: Path to notebook

        Returns:
            KernelActionResult with the kernel ID

        Raises:
            ValueError: If the notebook has no running kernel
        """
        notebook_path = normalize_path(notebook_path)
        kernel_id = await self._require_running_kernel(notebook_path)
        serverapp = require_server_context()
        await ensure_async(serverapp.kernel_manager.interrupt_kernel(kernel_id))
        logger.info(f"Interrupted kernel {kernel_id} for {notebook_path}")
        return KernelActionResult(
            kernel_id=kernel_id,
            message=(
                "Interrupt sent: the running cell stops with KeyboardInterrupt; "
                "variables are kept"
            ),
        )

    async def restart_notebook_kernel(self, notebook_path: str) -> KernelActionResult:
        """Restart the notebook's kernel (same kernel ID, all state cleared).

        Args:
            notebook_path: Path to notebook

        Returns:
            KernelActionResult with the kernel ID

        Raises:
            ValueError: If the notebook has no running kernel
        """
        notebook_path = normalize_path(notebook_path)
        kernel_id = await self._require_running_kernel(notebook_path)
        serverapp = require_server_context()
        await ensure_async(serverapp.kernel_manager.restart_kernel(kernel_id))
        logger.info(f"Restarted kernel {kernel_id} for {notebook_path}")
        return KernelActionResult(
            kernel_id=kernel_id,
            message=(
                "Kernel restarted: all variables and imports were cleared; "
                "re-run setup cells before continuing"
            ),
        )

    async def _read_notebook_kernel_name(self, notebook_path: str) -> str | None:
        """Read the kernelspec name from notebook metadata, if present."""

        try:
            nb = await load_notebook(notebook_path)
        except Exception:  # noqa: BLE001 - unreadable notebook: no kernelspec hint
            return None
        return nb.metadata.get("kernelspec", {}).get("name")

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

        Raises:
            ValueError: If kernel_name matches no installed kernel (checked
                before the old kernel is touched)
            KernelStartError: If the new kernel could not be started
        """
        notebook_path = normalize_path(notebook_path)
        available_kernels = await self.list_notebook_kernels()
        kernel_name = resolve_kernel_name(
            kernel_name, available_kernels.get("kernelspecs", {})
        )

        old_kernel_id = await self._stop_notebook_kernel(notebook_path)

        # Start new kernel (this will also create a new session)
        new_kernel_id = await self._start_kernel_for_notebook(
            notebook_path, kernel_name
        )

        # Update notebook metadata
        await self._update_notebook_kernel_metadata(notebook_path, kernel_name)

        return KernelSwitchResult(
            kernel_name=kernel_name,
            kernel_id=new_kernel_id,
            previous_kernel_id=old_kernel_id,
        )

    async def shutdown_notebook(
        self, notebook_path: str, shutdown_kernel: bool = True
    ) -> ShutdownResult:
        """Close the notebook's session and shut its kernel down.

        Deleting a Jupyter session always shuts its kernel down
        (jupyter_server's SessionManager.delete_session), so with
        ``shutdown_kernel=False`` the session is kept too and only this
        client's tracking is dropped.

        Args:
            notebook_path: Path to notebook
            shutdown_kernel: Whether to shut the kernel (and session) down

        Returns:
            ShutdownResult with status message and the notebook's kernel ID
        """
        notebook_path = normalize_path(notebook_path)

        if not shutdown_kernel:
            kernel_id = await self.find_kernel_for_notebook(notebook_path)
            self.kernel_tracker.untrack(notebook_path)
            return ShutdownResult(
                message=(
                    "Kernel left running: its Jupyter session was kept, because "
                    "closing a session always shuts its kernel down"
                ),
                kernel_id=kernel_id,
            )

        kernel_id = await self._stop_notebook_kernel(notebook_path)
        if kernel_id:
            return ShutdownResult(
                message="Notebook session closed and kernel shut down",
                kernel_id=kernel_id,
            )
        return ShutdownResult(
            message="No kernel was running for this notebook", kernel_id=None
        )

    async def _stop_notebook_kernel(self, notebook_path: str) -> str | None:
        """Delete the notebook's session and stop any kernel it still uses.

        Deleting the session shuts the session's kernel down; a tracked kernel
        that is still alive afterwards (one without a session) is shut down
        explicitly. A kernel is never shut down twice.

        Args:
            notebook_path: Path to notebook (already normalized)

        Returns:
            The kernel the notebook was using (the session's, else the
            tracked one), or None if it had none.
        """
        session = await self._find_session_for_notebook(notebook_path)
        session_kernel_id = (session or {}).get("kernel", {}).get("id")
        tracked_id = self.kernel_tracker.get(notebook_path)

        await self._delete_session_for_notebook(notebook_path)
        if tracked_id and is_kernel_alive(get_server_context(), tracked_id):
            await self._shutdown_kernel(tracked_id)
        self.kernel_tracker.untrack(notebook_path)
        return session_kernel_id or tracked_id

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

            logger.debug(f"Checking for existing session for: {notebook_path}")
            logger.debug(f"Found {len(sessions)} active sessions")

            matches: list[dict[str, Any]] = []

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
                # 3. Match using samefile (for symlinks/relative paths)
                #
                # Deliberately NO filename-only matching: two notebooks with
                # the same name in different folders must not share a kernel
                # (code would run in one project's kernel while outputs land
                # in the other's file).

                path_match = False

                # Strategy 1: Exact match
                if session_path == notebook_path:
                    path_match = True
                    logger.debug("  -> Matched via exact path comparison")

                else:
                    # Strategy 2: Both normalized.  Guarded per-session: a
                    # non-notebook session (e.g., a console with a non-.ipynb
                    # path) raises ValueError from normalize_path and must
                    # not abort the scan of the remaining sessions.
                    try:
                        if normalize_path(session_path) == notebook_path:
                            path_match = True
                            logger.debug("  -> Matched via normalized path comparison")
                    except ValueError:
                        logger.debug(
                            f"  -> Session path '{session_path}' is not a "
                            f"notebook path; skipping normalization"
                        )

                # Strategy 3: samefile (handles symlinks, relative paths)
                if not path_match:
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
                    matches.append(session)

            if matches:
                best = self._pick_session(notebook_path, matches)
                if len(matches) > 1:
                    logger.warning(
                        f"{len(matches)} sessions exist for {notebook_path} "
                        f"({[m.get('kernel', {}).get('id') for m in matches]}); "
                        f"using {best.get('kernel', {}).get('id')}. Duplicate sessions "
                        f"mean a kernel was started without tearing down the previous "
                        f"one -- the agent and JupyterLab can end up on different kernels."
                    )
                else:
                    logger.info(
                        f"Found existing session for {notebook_path} with kernel "
                        f"{best.get('kernel', {}).get('id')}"
                    )
                return best

            logger.debug(
                f"No existing session found for {notebook_path} "
                f"(checked {len(sessions)} sessions)"
            )

        except Exception as e:
            logger.warning(f"Error checking for existing session: {e}", exc_info=True)

        return None

    def _pick_session(
        self, notebook_path: str, matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Choose which session to attach to when several match one notebook.

        Returning "whichever came first out of list_sessions()" is not good enough:
        with duplicates present, this client and JupyterLab can settle on different
        kernels for the same notebook, so the user edits state the agent cannot see
        and vice versa. Preference order:

        1. the kernel already tracked for this notebook (keeps this client stable),
        2. the session with the most websocket connections -- that is the one a
           JupyterLab tab is actually looking at, so the human wins the tie,
        3. the most recently active session.
        """
        tracked_id = self.kernel_tracker.get(notebook_path)

        def rank(session: dict[str, Any]) -> tuple[int, int, str]:
            kernel = session.get("kernel", {}) or {}
            return (
                1 if kernel.get("id") == tracked_id else 0,
                kernel.get("connections") or 0,
                str(kernel.get("last_activity") or ""),
            )

        return max(matches, key=rank)

    async def _start_kernel_for_notebook(
        self, notebook_path: str, kernel_name: str
    ) -> str:
        """Start a kernel and track it for a notebook.

        Creates a Jupyter session which automatically creates and links a kernel
        to the notebook path. This ensures JupyterLab can discover and reuse the kernel.

        This approach avoids race conditions by letting SessionManager handle kernel
        creation atomically.

        Args:
            notebook_path: Path to notebook
            kernel_name: Kernel name

        Returns:
            Kernel ID

        Raises:
            KernelStartError: With the reason, e.g. a path Jupyter's contents
                manager cannot open (outside root_dir, hidden) or a failed
                session/kernel start
        """
        serverapp = get_server_context()
        if not serverapp:
            msg = "Jupyter server context not available; cannot start a kernel"
            raise KernelStartError(msg)

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

            # Preflight: ensure contents manager can see the file as a notebook.
            # Jupyter Server's default contents manager is async, so the call
            # must be awaited (ensure_async also accepts sync managers);
            # content=False checks existence without reading the notebook.
            root_dir = Path(serverapp.root_dir).resolve()
            try:
                await ensure_async(
                    contents_manager.get(api_path, content=False, type="notebook")
                )
            except Exception as e:
                msg = (
                    f"Cannot start a kernel for {notebook_path}: Jupyter's "
                    f"contents manager cannot open it ({e}). Jupyter sessions "
                    f"only exist for notebooks inside the server root "
                    f"({root_dir}) and outside hidden directories."
                )
                raise KernelStartError(msg) from e

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

        except KernelStartError:
            raise
        except Exception as e:
            logger.exception(f"Session/kernel creation failed for {notebook_path}")
            msg = f"Cannot start a {kernel_name} kernel for {notebook_path}: {e}"
            raise KernelStartError(msg) from e

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
        except Exception as e:  # noqa: BLE001 - best-effort shutdown
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

    async def _update_notebook_kernel_metadata(
        self, notebook_path: str, kernel_name: str
    ) -> None:
        """Update notebook's kernelspec metadata.

        For notebooks open in JupyterLab the metadata is updated through the
        live YDoc — a direct disk write while the notebook is open would
        trigger JupyterLab's stale-file reload (scroll-to-top, potential cell
        duplication after reconnect).  Only closed notebooks are written to
        disk, and that write runs in a worker thread.

        Args:
            notebook_path: Path to notebook
            kernel_name: New kernel name
        """
        try:
            available_kernels = await self.list_notebook_kernels()
            language_name, display_name = self._get_kernel_display_info(
                kernel_name, available_kernels.get("kernelspecs", {})
            )
            kernelspec = {
                "name": kernel_name,
                "display_name": display_name,
                "language": language_name,
            }

            # Open notebook: update through YDoc only
            from jupyter_mcp_executor.ydoc_helpers import (  # noqa: PLC0415
                ensure_ynotebook,
            )

            ydoc = await ensure_ynotebook(notebook_path)
            if ydoc is not None and self._set_ydoc_kernelspec(ydoc, kernelspec):
                logger.info(f"Updated kernel metadata via YDoc for {notebook_path}")
                return

            # Closed notebook: save through the contents manager
            async with editing_notebook(notebook_path) as nb:
                nb.metadata["kernelspec"] = kernelspec
            logger.info(f"Updated kernel metadata in {notebook_path}")

        except Exception as e:  # noqa: BLE001 - metadata update is best-effort
            logger.warning(f"Could not update notebook kernel metadata: {e}")

    @staticmethod
    def _set_ydoc_kernelspec(ydoc: Any, kernelspec: dict[str, str]) -> bool:
        """Set the kernelspec in a live YNotebook's metadata map.

        jupyter_ydoc has no public setter for one metadata key (``set()``
        replaces the whole document), so this writes its meta map ``_ymeta``.
        Returns False when that fails so the caller can fall back to a disk
        write.
        """
        try:
            from pycrdt import Map  # noqa: PLC0415

            ymeta = ydoc._ymeta
            metadata = ymeta.get("metadata")
            if metadata is None:
                ymeta["metadata"] = Map({"kernelspec": kernelspec})
            else:
                metadata["kernelspec"] = kernelspec
        except Exception as e:  # noqa: BLE001 - caller falls back to disk write
            # Loud on purpose: the disk write lands behind the open notebook,
            # and JupyterLab reloads it from the file.
            logger.warning(f"Could not set kernelspec via YDoc: {e!r}")
            return False
        return True
