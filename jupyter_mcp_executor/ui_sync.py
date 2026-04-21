"""Best-effort UI synchronization utilities.

Provides fire-and-forget helpers that nudge the JupyterLab browser UI after
a backend cell mutation.  All functions are non-critical: failures are
swallowed so they never block notebook operations.

Architecture
------------
``ui_scroll_to_cell`` uses a two-layer approach:

1. **YDoc layer** (in-memory, immediate, no browser needed):
   Set ``activeCellIndex`` in YDoc metadata so JupyterLab activates the cell
   when it processes the next YDoc update.  Handled in ``YDocAdapter``.

2. **Command bridge layer** (browser-dependent, best-effort):
   Fire ``notebook:scroll-cell-center`` via ``jupyterlab_commands_toolkit``'s
   ``execute_command``.  This emits a Jupyter server event that the
   ``jupyterlab_commands_toolkit`` frontend JS extension picks up and
   dispatches into JupyterLab's command registry, scrolling the active cell
   to the center of the viewport.

   Requires:
   - ``jupyterlab_commands_toolkit`` installed in the Python env
   - A browser tab with JupyterLab open (the extension must be loaded)

   Silently no-ops in headless mode or when the extension is not installed.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# How long to wait for the browser to respond.  Keep short so MCP tool calls
# aren't blocked when no browser is open.
_SCROLL_TIMEOUT_S = 1.5


async def ui_scroll_to_cell(position: int) -> None:
    """Scroll the JupyterLab viewport to the cell at *position*.

    Fires ``notebook:scroll-cell-center`` via the
    ``jupyterlab_commands_toolkit`` event bridge.  That command scrolls
    whatever cell is currently *active* in JupyterLab to the center of the
    viewport.  The caller is responsible for having already set the active
    cell (e.g. via ``ymeta["activeCellIndex"] = position`` in YDoc).

    This call is entirely non-blocking from the caller's perspective:
    - If no browser is open → times out after 1.5 s, returns silently.
    - If ``jupyterlab_commands_toolkit`` is not installed → returns silently.
    - If the command fails for any reason → returns silently.

    Args:
        position: 0-based cell index (used only for logging).
    """
    try:
        # Late import — optional dependency; do not fail at module load time.
        from jupyterlab_commands_toolkit.tools import execute_command  # noqa: PLC0415

        await asyncio.wait_for(
            execute_command("notebook:scroll-cell-center"),
            timeout=_SCROLL_TIMEOUT_S,
        )
        logger.debug("Scrolled JupyterLab viewport to cell %d", position)
    except Exception:
        # Non-critical: headless mode, browser closed, extension not loaded,
        # command not found, etc.  Never propagate.
        pass
