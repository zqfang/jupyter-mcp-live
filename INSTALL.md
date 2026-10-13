# Installation Guide for Jupyter MCP Executor

> **Not on PyPI yet.** `jupyter-mcp-executor` has not been published to PyPI,
> so `pip install jupyter-mcp-executor` will fail. Install it from GitHub
> (Option 1) or from a local clone (Option 2).

This guide uses [uv](https://github.com/astral-sh/uv), a fast Python package
installer and resolver; plain `pip` works too (see the end of this guide).

## Prerequisites

- Python 3.11 or higher
- Git

## Installation Options

### Option 1: Install from GitHub (for users)

Install the latest `main` into the environment that runs Jupyter:

```bash
# Install uv (skip if you already have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install jupyter-mcp-executor straight from GitHub
uv pip install "git+https://github.com/zqfang/jupyter-mcp-executor.git"
```

To pin a specific version, add a tag or commit, e.g.
`"git+https://github.com/zqfang/jupyter-mcp-executor.git@<tag-or-commit>"`.

The Jupyter Server extension is enabled automatically on install.

### Option 2: Development installation (from a clone)

For working on the code, or running the tests:

```bash
# Clone the repository
git clone https://github.com/zqfang/jupyter-mcp-executor.git
cd jupyter-mcp-executor

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
uv pip list | grep jupyter-mcp-executor

# Check that the Jupyter Server extension is enabled
jupyter server extension list
```

You should see `jupyter_mcp_executor` listed as enabled.

## Quick Start After Installation

### 1. Create a Configuration File

Create a `jupyter_config.py` file in your project directory or in `~/.jupyter/`
(see `examples/jupyter_config_with_execution_tools.py` for a fuller example):

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"

# Jupyter's token also authenticates MCP clients
c.IdentityProvider.token = "change-me"

# Register the notebook tools
c.MCPExtensionApp.mcp_tools = [
    f"jupyter_mcp_executor.execution_tools:{name}"
    for name in [
        "list_notebook_kernels", "list_notebook_sessions", "setup_notebook",
        "switch_notebook_kernel", "shutdown_notebook",
        "interrupt_notebook_kernel", "restart_notebook_kernel", "view_notebook",
        "read_cell", "add_code_cell", "edit_code_cell", "add_markdown_cell",
        "edit_markdown_cell", "execute_cell", "delete_cell",
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
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

For connecting MCP clients (Claude Code, Gemini CLI, …), see the README.
For JupyterHub deployments, see [JUPYTERHUB.md](JUPYTERHUB.md).

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

From a development installation (Option 2):

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_mcp_server.py -v

# Lint (the version CI uses is pinned in pyproject.toml)
ruff check jupyter_mcp_executor
ruff format --check jupyter_mcp_executor
```

For coverage, install `pytest-cov` first (`uv pip install pytest-cov`), then
run `pytest --cov=jupyter_mcp_executor tests/`.

## Updating

### Installed from GitHub (Option 1)

```bash
uv pip install --reinstall "git+https://github.com/zqfang/jupyter-mcp-executor.git"
```

### Development installation (Option 2)

```bash
cd jupyter-mcp-executor
git pull origin main
uv sync --extra test
```

## Troubleshooting

### Extension Not Loading

If `jupyter_mcp_executor` doesn't appear in `jupyter server extension list`,
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
uv pip uninstall jupyter-mcp-executor
```

## Alternative: Using pip

```bash
# Install from GitHub
pip install "git+https://github.com/zqfang/jupyter-mcp-executor.git"

# Development installation
git clone https://github.com/zqfang/jupyter-mcp-executor.git
cd jupyter-mcp-executor
pip install -e ".[test]"
```

## Additional Resources

- [uv Documentation](https://github.com/astral-sh/uv)
- [Jupyter Server Extension Guide](https://jupyter-server.readthedocs.io/en/latest/developers/extensions.html)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [FastMCP Documentation](https://gofastmcp.com/)
