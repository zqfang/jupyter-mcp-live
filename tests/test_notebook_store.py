"""Closed notebooks are read and saved through Jupyter's contents manager.

As JupyterLab's own save: the notebook is signed when every risky output is
trusted, its first checkpoint is created, save hooks run (e.g. jupytext
pairing), and the contents manager's rules (allow_hidden, non-filesystem
backends) apply.
"""

import asyncio
import json
from pathlib import Path

import nbformat
import pytest
from jupyter_core.utils import ensure_async
from jupyter_server.services.contents.largefilemanager import (
    AsyncLargeFileManager,
    LargeFileManager,
)
from nbformat.sign import NotebookNotary
from nbformat.v4 import new_code_cell, new_notebook, new_output

from jupyter_mcp_executor.nbformat_adapter import NbformatAdapter
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.notebook_store import (
    editing_notebook,
    ensure_directory,
    load_notebook,
    notebook_exists,
)
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


@pytest.fixture(params=[AsyncLargeFileManager, LargeFileManager], ids=["async", "sync"])
def contents_manager(request, tmp_path):
    """Jupyter's default (async) contents manager and a sync one, both with a
    file-backed signature store as in production: its SQLite connection may
    only be used from the thread that opened it."""
    root = tmp_path / "root"
    root.mkdir()
    cm = request.param(root_dir=str(root))
    # Keep the signature DB and secret out of the user's Jupyter data dir.
    cm.notary = NotebookNotary(
        data_dir=str(tmp_path), db_file=str(tmp_path / "nbsignatures.db")
    )
    set_server_context(_ServerApp(root, cm))
    yield cm
    set_server_context(None)
    cm.notary.store.close()


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

    assert await ensure_async(contents_manager.list_checkpoints("nb.ipynb"))


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

    with pytest.raises(FileNotFoundError, match=r"No such notebook: \.private/nb"):
        await load_notebook(path)


@pytest.mark.asyncio
async def test_missing_notebook_is_a_plain_file_not_found(contents_manager):
    """Agents see "No such notebook", not Tornado's "HTTP 404: Not Found"."""
    with pytest.raises(FileNotFoundError, match=r"No such notebook: gone\.ipynb"):
        await load_notebook(f"{contents_manager.root_dir}/gone.ipynb")


@pytest.mark.asyncio
async def test_paths_outside_the_root_are_refused(contents_manager):
    """A contents manager would read an absolute path as relative to its root."""
    with pytest.raises(ValueError, match="outside the Jupyter root"):
        await load_notebook("/elsewhere/nb.ipynb")


@pytest.mark.asyncio
async def test_trust_marks_never_reach_the_file(contents_manager):
    path = _write(
        contents_manager, "nb.ipynb", new_notebook(cells=[new_code_cell("x")])
    )

    await NbformatAdapter(path).update_cell_outputs(0, 1, [HTML_OUTPUT])

    raw = json.loads(Path(path).read_text())
    assert "trusted" not in raw["cells"][0]["metadata"]


@pytest.mark.asyncio
async def test_editing_a_signed_notebook_keeps_it_signed(contents_manager):
    trusted = new_code_cell("x", outputs=[new_output(**HTML_OUTPUT)])
    path = _write(contents_manager, "nb.ipynb", new_notebook(cells=[trusted]))
    nb = _on_disk(path)
    contents_manager.notary.sign(nb)
    nbformat.write(nb, path)

    await NbformatAdapter(path).add_cell(CellContent(source="y", cell_type="code"))

    assert contents_manager.notary.check_signature(_on_disk(path))


@pytest.mark.asyncio
async def test_appending_an_output_does_not_trust_the_cells_old_outputs(
    contents_manager,
):
    untrusted = new_code_cell("x", outputs=[new_output(**HTML_OUTPUT)])
    path = _write(contents_manager, "nb.ipynb", new_notebook(cells=[untrusted]))

    await NbformatAdapter(path).append_cell_output(
        0, {"output_type": "stream", "name": "stdout", "text": "more\n"}
    )

    assert not contents_manager.notary.check_signature(_on_disk(path))


@pytest.mark.asyncio
async def test_callers_cannot_mark_cells_trusted(contents_manager):
    untrusted = new_code_cell("x", outputs=[new_output(**HTML_OUTPUT)])
    path = _write(contents_manager, "nb.ipynb", new_notebook(cells=[untrusted]))

    await NbformatAdapter(path).edit_cell(
        0,
        CellContent(
            source="x",
            cell_type="code",
            metadata={"trusted": True},
            outputs=[HTML_OUTPUT],
        ),
    )

    assert not contents_manager.notary.check_signature(_on_disk(path))


def _signed(cm, name: str, nb) -> str:
    path = _write(cm, name, nb)
    signed = _on_disk(path)
    cm.notary.sign(signed)
    nbformat.write(signed, path)
    return path


@pytest.mark.asyncio
async def test_replacement_outputs_do_not_inherit_the_cells_trust(contents_manager):
    """A cell loaded from a signed notebook is marked trusted; outputs a
    caller puts in its place must be judged on their own."""
    path = _signed(
        contents_manager, "nb.ipynb", new_notebook(cells=[new_code_cell("x")])
    )

    await NbformatAdapter(path).edit_cell(
        0, CellContent(source="y", cell_type="code", outputs=[HTML_OUTPUT])
    )

    assert not contents_manager.notary.check_signature(_on_disk(path))


@pytest.mark.asyncio
async def test_editing_a_cell_of_a_signed_notebook_keeps_it_signed(contents_manager):
    """What edit_code_cell does: new source, outputs cleared."""
    trusted = new_code_cell("x", outputs=[new_output(**HTML_OUTPUT)])
    path = _signed(contents_manager, "nb.ipynb", new_notebook(cells=[trusted]))

    await NbformatAdapter(path).edit_cell(0, CellContent(source="y", cell_type="code"))

    on_disk = _on_disk(path)
    assert on_disk.cells[0].outputs == []
    assert contents_manager.notary.check_signature(on_disk)


@pytest.mark.asyncio
async def test_a_failed_edit_saves_nothing(contents_manager):
    path = _write(
        contents_manager, "nb.ipynb", new_notebook(cells=[new_code_cell("x")])
    )
    before = Path(path).read_text()

    async def failing_edit():
        async with editing_notebook(path) as nb:
            nb.cells.clear()
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        await failing_edit()

    assert Path(path).read_text() == before


@pytest.mark.asyncio
async def test_notebook_exists(contents_manager):
    root = Path(contents_manager.root_dir)
    _write(contents_manager, "nb.ipynb", new_notebook())
    (root / "dir.ipynb").mkdir()

    assert await notebook_exists(str(root / "nb.ipynb"))
    assert not await notebook_exists(str(root / "missing.ipynb"))
    assert not await notebook_exists(str(root / "dir.ipynb"))


@pytest.mark.asyncio
async def test_ensure_directory_creates_missing_parents(contents_manager):
    root = Path(contents_manager.root_dir)
    (root / "a").mkdir()

    await ensure_directory(str(root / "a" / "b" / "c"))
    await ensure_directory(str(root / "a" / "b" / "c"))  # already there
    await ensure_directory(str(root))  # the root itself

    assert (root / "a" / "b" / "c").is_dir()
