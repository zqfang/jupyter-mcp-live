# Installation Guide for Jupyter Server MCP

This guide covers installation of the Jupyter Server MCP extension using [uv](https://github.com/astral-sh/uv), a fast Python package installer and resolver.

## Prerequisites

- Python 3.10 or higher
- Git (for development installation)

## Why uv?

uv is a modern, fast Python package installer that:
- Installs dependencies 10-100x faster than pip
- Provides better dependency resolution
- Uses less disk space with a global cache
- Offers a streamlined development workflow

## Installation Options

### Option 1: Install from PyPI (Recommended for Users)

If the package is published to PyPI:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install jupyter-mcp-executor
uv pip install jupyter-mcp-executor
```

### Option 2: Development Installation (Recommended for Contributors)

For local development with the latest code:

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/yourusername/jupyter-mcp-executor.git
cd jupyter-mcp-executor

# Create a virtual environment with uv
uv venv

# Activate the virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install the package in editable mode with all dependencies
uv pip install -e .
```

### Option 3: Development Installation with Test Dependencies

For running tests and contributing:

```bash
# After cloning and creating venv (see Option 2)
source .venv/bin/activate

# install the env
uv sync --prerelease=allow

# Install with test dependencies
uv pip install -e ".[test]"

# Or install with dev dependencies (includes ruff linter)
uv pip install -e ".[dev]"


# Run jupyter
jupyter lab --config examples/jupyter_config_with_execution_tools.py
```

## Verifying Installation

After installation, verify that the extension is properly installed:

```bash
# Check if the package is installed
uv pip list | grep jupyter-mcp-executor

# Start Jupyter and check if the extension loads
jupyter server extension list
```

You should see `jupyter_mcp_executor` in the list of enabled extensions.

## Quick Start After Installation

### 1. Create a Configuration File

Create a `jupyter_config.py` file in your project directory or in `~/.jupyter/`:

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"
c.MCPExtensionApp.mcp_port = 8080

# Register tools (examples)
c.MCPExtensionApp.mcp_tools = [
    # Standard library tools
    "os:getcwd",
    "json:dumps",
    "time:time",
]
```

### 2. Start Jupyter Server

```bash
# Start Jupyter Lab with the configuration
jupyter lab --config=jupyter_config.py
```

The MCP server will start automatically at `http://localhost:8080/mcp`.

### 3. Test the MCP Server

You can test if the MCP server is running:

```bash
curl http://localhost:8080/mcp
```

## Installing Additional Tools

The extension can work with external tool packages. Install them as needed:

```bash
# Jupyter AI Tools (for notebook and file system operations)
uv pip install jupyter-ai-tools

# JupyterLab Commands Toolkit
uv pip install jupyterlab-commands-toolkit
```

Then configure them in your `jupyter_config.py`:

```python
c.MCPExtensionApp.mcp_tools = [
    # Jupyter AI Tools - Notebook operations
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
    "jupyter_ai_tools.toolkits.notebook:edit_cell",

    # File system operations
    "jupyter_ai_tools.toolkits.file_system:read",
    "jupyter_ai_tools.toolkits.file_system:write",

    # JupyterLab Commands
    "jupyterlab_commands_toolkit.tools:list_all_commands",
]
```

## Running Tests

If you installed with test dependencies:

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest --cov=jupyter_mcp_executor tests/

# Run specific test file
pytest tests/test_mcp_server.py -v
```

## Updating the Package

### For PyPI Installation

```bash
uv pip install --upgrade jupyter-mcp-executor
```

### For Development Installation

```bash
cd jupyter-mcp-executor
git pull origin main
uv pip install -e ".[test,dev]"
```

## Troubleshooting

### Extension Not Loading

If the extension doesn't appear in `jupyter server extension list`:

```bash
# Reinstall in editable mode
uv pip install -e .

# Check Jupyter configuration
jupyter --paths
```

### Port Already in Use

If port 8080 is already in use, change it in your configuration:

```python
c.MCPExtensionApp.mcp_port = 8081  # Use a different port
```

### Import Errors

If you get import errors for tool modules:

```bash
# Make sure the tool packages are installed
uv pip install jupyter-ai-tools jupyterlab-commands-toolkit
```

### uv Command Not Found

If `uv` command is not recognized after installation:

```bash
# Add uv to your PATH (the installer usually does this automatically)
# For bash/zsh, add to ~/.bashrc or ~/.zshrc:
export PATH="$HOME/.cargo/bin:$PATH"

# Reload your shell
source ~/.bashrc  # or source ~/.zshrc
```

## Uninstallation

```bash
uv pip uninstall jupyter-mcp-executor
```

## Additional Resources

- [uv Documentation](https://github.com/astral-sh/uv)
- [Jupyter Server Extension Guide](https://jupyter-server.readthedocs.io/en/latest/developers/extensions.html)
- [Model Context Protocol Specification](https://modelcontextprotocol.io/)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)

## Alternative: Using pip

If you prefer to use pip instead of uv:

```bash
# PyPI installation
pip install jupyter-mcp-executor

# Development installation
git clone https://github.com/yourusername/jupyter-mcp-executor.git
cd jupyter-mcp-executor
pip install -e ".[test,dev]"
```

However, uv is recommended for its speed and improved dependency resolution.
