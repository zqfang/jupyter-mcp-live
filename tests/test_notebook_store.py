"""Closed notebooks are read and saved through Jupyter's contents manager.

As JupyterLab's own save: the notebook is signed when every risky output is
trusted, its first checkpoint is created, save hooks run (e.g. jupytext
pairing), and the contents manager's rules (allow_hidden, non-filesystem
backends) apply.
"""

import asyncio
from pathlib import Path

import nbformat
import pytest
from jupyter_server.services.contents.largefilemanager import AsyncLargeFileManager
from nbformat.sign import NotebookNotary
from nbformat.v4 import new_code_cell, new_notebook, new_output

from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.notebook_store import load_notebook
from jupyter_mcp_executor.server_context import set_server_context

HTML_OUTPUT = {
    "output_type": "display_data",
    "data": {"text/html": "<b>hi</b>", "text/plain": "hi"},
    "metadata": {},
}


class _ServerApp:
    def __init__(self, root_dir, contents_manager):
        self.root_dir = str(root_dir)
        self.contents_manager = contents_manager


@pytest.fixture
def contents_manager(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cm = AsyncLargeFileManager(root_dir=str(root))
    # Keep the signature DB and secret out of the user's Jupyter data dir.
    cm.notary = NotebookNotary(data_dir=str(tmp_path), db_file=":memory:")
    set_server_context(_ServerApp(root, cm))
    yield cm
    set_server_context(None)


def _write(cm, name: str, nb) -> str:
    path = f"{cm.root_dir}/{name}"
    nbformat.write(nb, path)
    return path


def _on_disk(path: str):
    return nbformat.read(path, as_version=4)


@pytest.mark.asyncio
async def test_outputs_the_tools_produced_are_trusted(contents_manager):
    """JupyterLab trusts the outputs of cells it runs; so must we, or HTML
    and JavaScript outputs from MCP runs are sanitized in JupyterLab."""
    path = _write(
        contents_manager, "nb.ipynb", new_notebook(cells=[new_code_cell("x")])
    )
    adapter = NbformatAdapter(path)

    await adapter.update_cell_outputs(0, 1, [HTML_OUTPUT])

    assert contents_manager.notary.check_signature(_on_disk(path))


@pytest.mark.asyncio
async def test_untrusted_outputs_stay_untrusted(contents_manager):
    """Editing another cell must not sign outputs nobody trusted."""
    untrusted = new_code_cell("x", outputs=[new_output(**HTML_OUTPUT)])
    path = _write(contents_manager, "nb.ipynb", new_notebook(cells=[untrusted]))
    adapter = NbformatAdapter(path)

    await adapter.add_cell(CellContent(source="y = 2", cell_type="code"))

    on_disk = _on_disk(path)
    assert [c.source for c in on_disk.cells] == ["x", "y = 2"]
    assert not contents_manager.notary.check_signature(on_disk)


@pytest.mark.asyncio
async def test_saving_creates_a_checkpoint(contents_manager):
    path = _write(contents_manager, "nb.ipynb", new_notebook())

    await NbformatAdapter(path).add_cell(CellContent(source="x", cell_type="code"))

    assert await contents_manager.list_checkpoints("nb.ipynb")


@pytest.mark.asyncio
async def test_save_hooks_run(contents_manager):
    """e.g. jupytext writes the paired script in a post-save hook."""
    saved = []
    contents_manager.post_save_hook = lambda **kw: saved.append(kw["model"]["path"])
    path = _write(contents_manager, "nb.ipynb", new_notebook())

    await NbformatAdapter(path).add_cell(CellContent(source="x", cell_type="code"))

    assert saved == ["nb.ipynb"]


@pytest.mark.asyncio
async def test_reads_do_not_show_trust_marks(contents_manager):
    """The contents manager marks cells trusted/untrusted in memory; that is
    not part of the notebook."""
    path = _write(
        contents_manager, "nb.ipynb", new_notebook(cells=[new_code_cell("x")])
    )

    cell = await NbformatAdapter(path).read_cell(0)

    assert "trusted" not in cell.metadata


@pytest.mark.asyncio
async def test_concurrent_edits_are_not_lost(contents_manager):
    """Two read-modify-write calls on one notebook must not overwrite each other."""
    path = _write(contents_manager, "nb.ipynb", new_notebook())
    adapter = NbformatAdapter(path)

    await asyncio.gather(
        *(
            adapter.add_cell(CellContent(source=f"c{i}", cell_type="code"))
            for i in range(5)
        )
    )

    assert sorted(c.source for c in _on_disk(path).cells) == [f"c{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_hidden_notebooks_follow_the_contents_managers_rule(contents_manager):
    (Path(contents_manager.root_dir) / ".private").mkdir()
    path = _write(contents_manager, ".private/nb.ipynb", new_notebook())

    with pytest.raises(Exception, match="404"):
        await load_notebook(path)
