# Jupyter MCP Executor

A powerful MCP (Model Context Protocol) server for executing code in Jupyter notebooks. This extension enables AI assistants and other MCP clients to programmatically create, modify, and execute code across multiple kernel types (Python, R, Julia, and more) through a running Jupyter Server.


## Overview

Jupyter MCP Executor provides a comprehensive toolkit for AI-assisted coding workflows. It enables AI assistants to:

- **Execute code** in any Jupyter kernel (Python, R, Julia, etc.)
- **Create and modify** notebook cells programmatically
- **Manage kernels** - start, stop, switch between different kernel types
- **Query notebooks** - read cells, view outputs, check execution status
- **Install packages** directly into kernel environments

Perfect for building AI-powered data analysis tools, automated notebook workflows, and interactive coding assistants.

## Key Features

- **Multi-Kernel Support**: Execute code in Python, R, Julia, and any Jupyter-supported kernel
- **Collaborative Editing**: Full support for YDoc - works seamlessly when notebooks are open in JupyterLab
- **Kernel Management**: Automatic kernel reuse, smart kernel resolution, and session management
- **Complete Notebook Control**: Create, read, update, and delete cells programmatically
- **Output Filtering**: Intelligent filtering of large outputs to optimize token usage
- **MCP Protocol**: Standard MCP interface for AI assistant integration
- **Production Ready**: Comprehensive test suite, modular architecture, and detailed documentation



## Quick Start


### 1. Intallation

```bash
git clone https://github.com/zqfang/jupyter-mcp-executor
cd jupyter-mcp-executor
uv sync 
```

See [INSTALL.md](INSTALL.md) for detailed instructions.

### 2. Basic Configuration

Create a `jupyter_config.py` file:

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"

# set mcp port here
c.MCPExtensionApp.mcp_port = 8080

# Register tools from existing packages
c.MCPExtensionApp.mcp_tools = [
    # Notebook execution and kernel management tools
    "jupyter_mcp_executor.execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.execution_tools:execute_cell",
    "jupyter_mcp_executor.execution_tools:setup_notebook",
    "jupyter_mcp_executor.execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.execution_tools:add_code_cell",
    "jupyter_mcp_executor.execution_tools:edit_code_cell",
    "jupyter_mcp_executor.execution_tools:add_markdown_cell",
    "jupyter_mcp_executor.execution_tools:edit_markdown_cell",
    "jupyter_mcp_executor.execution_tools:delete_cell",
    "jupyter_mcp_executor.execution_tools:view_notebook",
    "jupyter_mcp_executor.execution_tools:read_cell"
]
```

### 3. Start Jupyter Server

```bash
jupyter lab --config jupyter_config.py
## or use example config here
# jupyter lab --config examples/jupyter_config_with_excution_tools.py
```

The MCP server will start automatically on `http://localhost:8080/mcp`.

### 4. Connect MCP Clients

**Claude Code Configuration:**

Set the following configuration:

```json
"mcpServers": {
  "jupyter-mcp": {
    "type": "http",
    "url": "http://localhost:8080/mcp"
  }
}
```

Or use the `claude` CLI:

```bash
claude mcp add --transport http jupyter-mcp http://localhost:8080/mcp
```

**Gemini CLI Configuration:**

Add the following to `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "httpUrl": "http://localhost:8080/mcp"
    }
  }
}
```

## Architecture

### Core Components

#### MCPServer (`jupyter_mcp_executor.mcp_server.MCPServer`)

A simplified LoggingConfigurable class that manages FastMCP integration:

```python
from jupyter_mcp_executor.mcp_server import MCPServer

# Create server
server = MCPServer(name="My Server", port=8080)

# Register functions
def my_tool(message: str) -> str:
    return f"Hello, {message}!"

server.register_tool(my_tool)

# Start server
await server.start_server()
```

**Key Methods:**
- `register_tool(func, name=None, description=None)` - Register a Python function
- `register_tools(tools)` - Register multiple functions (list or dict)
- `list_tools()` - Get list of registered tools
- `start_server(host=None)` - Start the HTTP MCP server

#### MCPExtensionApp (`jupyter_mcp_executor.extension.MCPExtensionApp`)

Jupyter Server extension that manages the MCP server lifecycle:

**Configuration Traits:**
- `mcp_name` - Server name (default: "Jupyter MCP Server")
- `mcp_port` - Server port (default: 3001)
- `mcp_tools` - List of tools to register (format: "module:function")
- `use_tool_discovery` - Enable automatic tool discovery via entrypoints (default: True)

### Tool Registration

Tools can be registered in two ways:

#### 1. Manual Configuration

Specify tools directly in your Jupyter configuration using `module:function` format:

```python
c.MCPExtensionApp.mcp_tools = [
    "os:getcwd",
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
]
```

#### 2. Automatic Discovery via Entrypoints

Python packages can expose tools automatically using the `jupyter_mcp_executor.tools` entrypoint group.

**In your package's `pyproject.toml`:**

```toml
[project.entry-points."jupyter_mcp_executor.tools"]
my_package_tools = "my_package.tools:TOOLS"
```

**In `my_package/tools.py`:**

```python
# Option 1: Define as a list
TOOLS = [
    "my_package.operations:create_file",
    "my_package.operations:delete_file",
]

# Option 2: Define as a function
def get_tools():
    return [
        "my_package.operations:create_file",
        "my_package.operations:delete_file",
    ]
```

Tools from entrypoints are discovered automatically when the extension starts. To disable automatic discovery:

```python
c.MCPExtensionApp.use_tool_discovery = False
```

## Configuration Examples

### Minimal Setup
```python
c = get_config()
c.MCPExtensionApp.mcp_port = 8080
```

### Full Configuration
```python
c = get_config()

# MCP Server Configuration
c.MCPExtensionApp.mcp_name = "Advanced Jupyter MCP Server"
c.MCPExtensionApp.mcp_port = 8080
c.MCPExtensionApp.mcp_tools = [
    # File system operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.file_system:read",
    "jupyter_ai_tools.toolkits.file_system:write", 
    "jupyter_ai_tools.toolkits.file_system:edit",
    "jupyter_ai_tools.toolkits.file_system:ls",
    "jupyter_ai_tools.toolkits.file_system:glob",
    
    # Notebook operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.notebook:read_notebook",
    "jupyter_ai_tools.toolkits.notebook:edit_cell",
    "jupyter_ai_tools.toolkits.notebook:add_cell", 
    "jupyter_ai_tools.toolkits.notebook:delete_cell",
    "jupyter_ai_tools.toolkits.notebook:create_notebook",
    
    # Git operations (jupyter-ai-tools)
    "jupyter_ai_tools.toolkits.git:git_status",
    "jupyter_ai_tools.toolkits.git:git_add",
    "jupyter_ai_tools.toolkits.git:git_commit",
    "jupyter_ai_tools.toolkits.git:git_push",
    
    # JupyterLab operations (jupyterlab-commands-toolkit)
    "jupyterlab_commands_toolkit.tools:clear_all_outputs_in_notebook",
    "jupyterlab_commands_toolkit.tools:open_document",
    "jupyterlab_commands_toolkit.tools:open_markdown_file_in_preview_mode",
    "jupyterlab_commands_toolkit.tools:show_diff_of_current_notebook",
    
    # Utility functions  
    "os:getcwd",
    "json:dumps",
    "time:time",
    "platform:system",
]
```

### Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest --cov=jupyter_mcp_executor tests/
```

### Project Structure

```
jupyter_mcp_executor/
├── jupyter_mcp_executor/
│   ├── __init__.py
│   ├── mcp_server.py      # Core MCP server implementation
│   └── extension.py       # Jupyter Server extension
├── tests/
│   ├── test_mcp_server.py # MCPServer tests
│   └── test_extension.py  # Extension tests  
├── demo/
│   ├── jupyter_config.py  # Example configuration
│   └── *.py              # Debug/diagnostic scripts
└── pyproject.toml         # Package configuration
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality  
4. Ensure all tests pass: `pytest tests/`
5. Submit a pull request
