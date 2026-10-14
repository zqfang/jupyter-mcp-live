"""Without an explicit kernel name, start the server's default kernel.

``MappingKernelManager.default_kernel_name`` is what JupyterLab offers for a
new notebook; a server configured for R (or without python3) must not get
python3 kernels from the MCP tools.
"""

from unittest.mock import AsyncMock, patch

import pytest

from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.kernel_utils import default_kernel_name
from tests.test_kernel_reuse import FakeKernelSpecManager, FakeServerApp


class RKernelSpecManager(FakeKernelSpecManager):
    def get_all_specs(self):
        return {
            **super().get_all_specs(),
            "ir": {"spec": {"language": "R", "display_name": "R"}},
        }


def _r_default_app() -> FakeServerApp:
    app = FakeServerApp()
    app.kernel_manager.default_kernel_name = "ir"
    app.kernel_spec_manager = RKernelSpecManager()
    return app


def test_default_kernel_name_is_the_servers():
    assert default_kernel_name(_r_default_app()) == "ir"


def test_default_kernel_name_without_a_server_is_python3():
    assert default_kernel_name(None) == "python3"
    assert default_kernel_name(FakeServerApp()) == "python3"


def _patched(app):
    return (
        patch(
            "jupyter_mcp_executor.kernel_manager.get_server_context", return_value=app
        ),
        patch(
            "jupyter_mcp_executor.kernel_manager.require_server_context",
            return_value=app,
        ),
    )


@pytest.mark.asyncio
async def test_setup_notebook_starts_the_servers_default_kernel(tmp_path):
    path = str(tmp_path / "new.ipynb")
    app = _r_default_app()
    mgr = KernelManager()
    start = AsyncMock(return_value="k1")

    first, second = _patched(app)
    with first, second, patch.object(mgr, "_start_kernel_for_notebook", start):
        result = await mgr.setup_notebook(path)

    start.assert_awaited_once_with(path, "ir")
    assert result.kernel_name == "ir"


@pytest.mark.asyncio
async def test_auto_start_without_kernelspec_uses_the_servers_default(tmp_path):
    """Executing in a notebook whose metadata names no kernel."""
    nb = tmp_path / "nb.ipynb"
    nb.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    app = _r_default_app()
    mgr = KernelManager()
    start = AsyncMock(return_value="k1")

    first, second = _patched(app)
    with first, second, patch.object(mgr, "_start_kernel_for_notebook", start):
        await mgr.get_kernel_for_notebook(str(nb))

    start.assert_awaited_once_with(str(nb), "ir")


@pytest.mark.asyncio
async def test_auto_start_with_an_uninstalled_default_lists_the_kernels(tmp_path):
    nb = tmp_path / "nb.ipynb"
    nb.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    app = FakeServerApp()  # python3 only
    app.kernel_manager.default_kernel_name = "ir"
    mgr = KernelManager()
    start = AsyncMock(return_value="k1")

    first, second = _patched(app)
    with (
        first,
        second,
        patch.object(mgr, "_start_kernel_for_notebook", start),
        pytest.raises(ValueError, match="Available kernels"),
    ):
        await mgr.get_kernel_for_notebook(str(nb))

    start.assert_not_awaited()
