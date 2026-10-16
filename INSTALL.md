# Installation Guide for Jupyter MCP Live

This guide uses [uv](https://github.com/astral-sh/uv), a fast Python package
installer and resolver; plain `pip` works too (see the end of this guide).

## Prerequisites

- Python 3.11 or higher
- Git (only for Options 2 and 3)

## Installation Options

In every option, install into the environment that runs Jupyter. The Jupyter
Server extension is enabled automatically on install.

### Option 1: Install from PyPI (recommended)

```bash
# Install uv (skip if you already have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

uv pip install jupyter-mcp-live
```

### Option 2: Install the latest `main` from GitHub

For changes not released yet:

```bash
uv pip install "git+https://github.com/zqfang/jupyter-mcp-live.git"
```

To pin a tag or commit, append it, e.g.
`"git+https://github.com/zqfang/jupyter-mcp-live.git@<tag-or-commit>"`.

### Option 3: Development installation (from a clone)

For working on the code, or running the tests:

```bash
# Clone the repository
git clone https://github.com/zqfang/jupyter-mcp-live.git
cd jupyter-mcp-live

# Create .venv and install the package in editable mode with test tools
# (pytest, pytest-jupyter, ruff)
uv sync --extra test

# Activate the virtual environment
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# Run Jupyter with the example configuration
jupyter lab --config examples/jupyter_config_with_execution_tools.py
```

## Verifying Installation

```bash
# Check that the package is installed
uv pip list | grep jupyter-mcp-live

# Check that the Jupyter Server extension is enabled
jupyter server extension list
```

You should see `jupyter_mcp_live` listed as enabled.

## Quick Start After Installation

### 1. Create a Configuration File

Create a `jupyter_config.py` file in your project directory or in `~/.jupyter/`
(see `examples/jupyter_config_with_execution_tools.py` for a fuller example):

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"

# Jupyter's token also authenticates MCP clients: set JUPYTER_TOKEN in the
# environment before starting Jupyter instead of writing it here

# Register the notebook tools
c.MCPExtensionApp.mcp_tools = [
    f"jupyter_mcp_live.execution_tools:{name}"
    for name in [
        "list_notebook_kernels", "list_notebook_sessions", "setup_notebook",
        "switch_notebook_kernel", "shutdown_notebook",
        "interrupt_notebook_kernel", "restart_notebook_kernel", "view_notebook",
        "read_cell", "add_code_cell", "edit_code_cell", "add_markdown_cell",
        "edit_markdown_cell", "execute_cell", "delete_cell", "view_cell_image",
    ]
]
```

### 2. Start Jupyter Server

```bash
jupyter lab --config=jupyter_config.py
```

The MCP endpoint is served by Jupyter at `http://localhost:8888/mcp` (Jupyter's
port and base_url), and requires the Jupyter token.

### 3. Test the MCP Server

Send an MCP `initialize` request with the token (without it you get `403`):

```bash
curl -X POST http://localhost:8888/mcp \
  -H "Authorization: Bearer <your Jupyter token>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

### 4. Connect an MCP Client

For Claude Code:

```bash
claude mcp add --transport http jupyter-mcp http://localhost:8888/mcp \
  --header "Authorization: Bearer <your Jupyter token>"
```

For other clients (Gemini CLI, JSON `mcpServers` config), see
[Connect MCP Clients](README.md#4-connect-mcp-clients) in the README. For
JupyterHub deployments, see [JUPYTERHUB.md](JUPYTERHUB.md).

## Installing Additional Tools

The extension can also expose tools from other packages. Install them as needed:

```bash
# Jupyter AI Tools (notebook and file system operations)
uv pip install jupyter-ai-tools

# JupyterLab Commands Toolkit
uv pip install jupyterlab-commands-toolkit
```

Then add them to `mcp_tools` in your `jupyter_config.py`:

```python
c.MCPExtensionApp.mcp_tools = [
    # ... the notebook tools above, plus:
    "jupyter_ai_tools.toolkits.file_system:read",
    "jupyter_ai_tools.toolkits.file_system:write",
    "jupyterlab_commands_toolkit.tools:list_all_commands",
]
```

## Running Tests

From a development installation (Option 3):

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_mcp_server.py -v

# Lint (the version CI uses is pinned in pyproject.toml)
ruff check jupyter_mcp_live
ruff format --check jupyter_mcp_live
```

For coverage, install `pytest-cov` first (`uv pip install pytest-cov`), then
run `pytest --cov=jupyter_mcp_live tests/`.

## Updating

### Installed from PyPI (Option 1)

```bash
uv pip install --upgrade jupyter-mcp-live
```

### Installed from GitHub (Option 2)

```bash
uv pip install --reinstall "git+https://github.com/zqfang/jupyter-mcp-live.git"
```

### Development installation (Option 3)

```bash
cd jupyter-mcp-live
git pull origin main
uv sync --extra test
```

## Troubleshooting

### Extension Not Loading

If `jupyter_mcp_live` doesn't appear in `jupyter server extension list`,
make sure the package is installed in the same environment that runs Jupyter:

```bash
# Which Jupyter / Python is in use?
which jupyter python

# Check Jupyter configuration paths
jupyter --paths
```

### Port Already in Use

MCP is served on Jupyter's own port. If that port is taken, start Jupyter on
another one and point your MCP client at it:

```bash
jupyter lab --config=jupyter_config.py --ServerApp.port=8890
# MCP endpoint: http://localhost:8890/mcp
```

(`c.MCPExtensionApp.mcp_port` only applies to the optional, unauthenticated
standalone listener, which is off by default; see the README.)

### Import Errors

If you get import errors for tool modules:

```bash
# Make sure the tool packages are installed
uv pip install jupyter-ai-tools jupyterlab-commands-toolkit
```

### uv Command Not Found

If `uv` is not recognized after installation, add its install directory to
your PATH (the installer usually does this automatically):

```bash
# For bash/zsh, add to ~/.bashrc or ~/.zshrc:
export PATH="$HOME/.local/bin:$PATH"

# Reload your shell
source ~/.bashrc  # or source ~/.zshrc
```

## Uninstallation

```bash
uv pip uninstall jupyter-mcp-live
```

## Alternative: Using pip

```bash
# Install from PyPI
pip install jupyter-mcp-live

# Install the latest main from GitHub
pip install "git+https://github.com/zqfang/jupyter-mcp-live.git"

# Development installation
git clone https://github.com/zqfang/jupyter-mcp-live.git
cd jupyter-mcp-live
pip install -e ".[test]"
```

## Additional Resources

- [uv Documentation](https://github.com/astral-sh/uv)
- [Jupyter Server Extension Guide](https://jupyter-server.readthedocs.io/en/latest/developers/extensions.html)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [FastMCP Documentation](https://gofastmcp.com/)
