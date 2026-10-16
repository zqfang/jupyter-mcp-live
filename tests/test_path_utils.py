"""Notebook paths must be inside the Jupyter root directory.

Jupyter's contents manager cannot open notebooks outside root_dir, so they
can have no session (no kernel) and no YDoc room: the tools could only edit
the file, unseen by JupyterLab. Such paths are rejected up front instead.
"""

from types import SimpleNamespace

import pytest

from jupyter_mcp_live.path_utils import normalize_path
from jupyter_mcp_live.server_context import set_server_context


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    set_server_context(SimpleNamespace(root_dir=str(root)))
    yield root
    set_server_context(None)


def test_relative_and_absolute_paths_inside_the_root_are_accepted(root):
    expected = str((root / "sub" / "nb.ipynb").resolve())

    assert normalize_path("sub/nb.ipynb") == expected
    assert normalize_path(str(root / "sub" / "nb.ipynb")) == expected


def test_absolute_path_outside_the_root_is_rejected(root, tmp_path):
    outside = tmp_path / "elsewhere" / "nb.ipynb"

    with pytest.raises(ValueError, match="outside the Jupyter root directory"):
        normalize_path(str(outside))


def test_relative_path_escaping_the_root_is_rejected(root):
    with pytest.raises(ValueError, match="outside the Jupyter root directory"):
        normalize_path("../nb.ipynb")


def test_symlink_inside_the_root_pointing_outside_is_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")

    with pytest.raises(ValueError, match="outside the Jupyter root directory"):
        normalize_path("linked/nb.ipynb")


def test_without_a_server_absolute_paths_are_accepted(tmp_path):
    """No server context (library use, unit tests): there is no root to check."""
    set_server_context(None)
    path = tmp_path / "nb.ipynb"

    assert normalize_path(str(path)) == str(path.resolve())


def test_only_notebooks_are_accepted(root):
    with pytest.raises(ValueError, match=r"\.ipynb"):
        normalize_path("data.csv")
