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
- **Live Output Streaming**: Outputs render in JupyterLab as the kernel emits them - watch long-running cells progress in real time instead of waiting for the tool call to finish
- **Progress Notifications**: Long-running cells send MCP progress updates to the client (elapsed time, output count, latest output line), including heartbeats for silent cells
- **Kernel Management**: Automatic kernel reuse, smart kernel resolution, and session management
- **Complete Notebook Control**: Create, read, update, and delete cells programmatically
- **Output Filtering**: Intelligent filtering of large outputs to optimize token usage
- **Plots the Model Can See**: `view_cell_image` returns a cell's PNG/JPEG figures as MCP image content, also from inside Code Mode scripts
- **MCP Protocol**: Standard MCP interface for AI assistant integration
- **Authenticated by Jupyter**: The MCP endpoint lives on the Jupyter Server itself and accepts the same token as Jupyter — works on a local server and on JupyterHub
- **Production Ready**: Comprehensive test suite, modular architecture, and detailed documentation

## Quick Start

### 1. Installation

Not published to PyPI yet. Install from GitHub into the environment that runs
Jupyter:

```bash
uv pip install "git+https://github.com/zqfang/jupyter-mcp-executor.git"
```

Or, for development, from a clone:

```bash
git clone https://github.com/zqfang/jupyter-mcp-executor
cd jupyter-mcp-executor
uv sync --extra test
```

See [INSTALL.md](INSTALL.md) for detailed instructions.

### 2. Basic Configuration

Create a `jupyter_config.py` file:

```python
c = get_config()

# Basic MCP server settings
c.MCPExtensionApp.mcp_name = "My Jupyter MCP Server"

# Jupyter's token also authenticates MCP clients (pick your own secret)
c.IdentityProvider.token = "change-me"

# Register tools from existing packages
c.MCPExtensionApp.mcp_tools = [
    # Notebook execution and kernel management tools
    "jupyter_mcp_executor.execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.execution_tools:execute_cell",
    "jupyter_mcp_executor.execution_tools:setup_notebook",
    "jupyter_mcp_executor.execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.execution_tools:interrupt_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:restart_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:add_code_cell",
    "jupyter_mcp_executor.execution_tools:edit_code_cell",
    "jupyter_mcp_executor.execution_tools:add_markdown_cell",
    "jupyter_mcp_executor.execution_tools:edit_markdown_cell",
    "jupyter_mcp_executor.execution_tools:delete_cell",
    "jupyter_mcp_executor.execution_tools:view_notebook",
    "jupyter_mcp_executor.execution_tools:read_cell",
    "jupyter_mcp_executor.execution_tools:view_cell_image"
]
```

### 3. Start Jupyter Server

```bash
jupyter lab --config jupyter_config.py
## or use example config here
# jupyter lab --config examples/jupyter_config_with_excution_tools.py
```

The MCP endpoint is served by Jupyter itself at `{base_url}mcp` on the Jupyter
port, e.g. `http://localhost:8888/mcp`. Every request must carry the Jupyter
token (`Authorization: token <TOKEN>` or `Authorization: Bearer <TOKEN>`);
requests without it get `403`.

### 4. Connect MCP Clients

**Claude Code Configuration:**

Set the following configuration:

```json
"mcpServers": {
  "jupyter-mcp": {
    "type": "http",
    "url": "http://localhost:8888/mcp",
    "headers": { "Authorization": "Bearer <your Jupyter token>" }
  }
}
```

Or use the `claude` CLI:

```bash
claude mcp add --transport http jupyter-mcp http://localhost:8888/mcp \
  --header "Authorization: Bearer <your Jupyter token>"
```

**Gemini CLI Configuration:**

Add the following to `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "jupyter-mcp": {
      "httpUrl": "http://localhost:8888/mcp",
      "headers": { "Authorization": "Bearer <your Jupyter token>" }
    }
  }
}
```

### JupyterHub

Install `jupyter-mcp-executor` in the **single-user server environment** (the
user image). Each user's server then exposes its own endpoint through the Hub
proxy — no extra ports or proxy configuration:

```
https://<hub-host>/user/<username>/mcp
```

Authenticate with a JupyterHub API token: create one on the Hub's
**Token** page (`https://<hub-host>/hub/token`). A token you create for
yourself can reach your own server by default; if you restrict its
permissions, keep the scope `access:servers!user=<username>`. Send it as
`Authorization: Bearer <token>`:

```bash
claude mcp add --transport http jupyter-hub https://<hub-host>/user/<username>/mcp \
  --header "Authorization: Bearer <hub-api-token>"
```

Each user's MCP endpoint can only reach that user's kernels and files.

For the full step-by-step setup (user image, tool config, restart, token,
verification, troubleshooting), see [JUPYTERHUB.md](JUPYTERHUB.md).

### Long-running cells

A cell may run for up to 10 hours (`EXECUTION_CONFIG.default_timeout`). While
it runs, the server sends MCP progress notifications: one per output line (at
most one per second) and a heartbeat every 30s.

Your MCP client has its own limits. In Claude Code:

- **Idle timeout:** a tool call that receives neither a response nor progress
  for the idle window is aborted (`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, 5
  minutes for HTTP servers). The 30s heartbeat keeps long, silent cells from
  hitting it.
- **Wall-clock timeout:** `MCP_TOOL_TIMEOUT` (milliseconds, default about 28
  hours) or a per-server `timeout` in `.mcp.json`. Progress does not extend
  these, so if you set one, keep it above your longest cell, e.g.
  `MCP_TOOL_TIMEOUT=36000000` for 10 hours.

To stop a cell, call `interrupt_notebook_kernel` (like JupyterLab's stop
button; variables are kept) or, if it ignores the interrupt,
`restart_notebook_kernel` (clears all state). A cell that hits the timeout,
or whose MCP request is cancelled, is interrupted automatically.
On Windows, an interrupt only takes effect between Python statements, so a
single long call into C code (e.g. `time.sleep(600)` or one huge native
operation) finishes before it stops. JupyterLab's stop button behaves the
same. Use `restart_notebook_kernel` if a cell must stop immediately.

See [Claude Code&#39;s MCP docs](https://code.claude.com/docs/en/mcp) and
[environment variables](https://code.claude.com/docs/en/env-vars).

### Security notes

- MCP tools execute arbitrary code; anyone holding the token can run code as
  the Jupyter user. Treat the token like a password.
- If Jupyter runs **without** authentication (empty token and no password),
  Jupyter treats every request as anonymous and requires an XSRF cookie on
  `POST`, so MCP clients get `403`. That is deliberate — an unauthenticated
  server is open to cross-site attacks. Set a token instead.
- Send the token in the `Authorization` header, not as `?token=` in the URL
  (URLs end up in logs and shell history).
- The endpoint is meant for MCP clients (Claude Code, Gemini CLI, …), not
  browser pages: CORS preflight requests are not answered.
- Use `c.MCPExtensionApp.mcp_endpoint_path` to move the endpoint if another
  extension already serves `/mcp`.

### Upgrading from 0.3.x

0.4.0 serves MCP on the Jupyter port by default and **no longer starts the
separate listener on `mcp_port`**. Point clients at `http://<jupyter>/mcp` with
the Jupyter token, as above. To keep the old unauthenticated listener on a
single-user machine, set:

```python
c.MCPExtensionApp.mcp_standalone = True  # listens on mcp_port, NO authentication
c.MCPExtensionApp.mcp_port = 8080
```

The standalone listener now validates the `Host` and `Origin` headers (421 /
403 for foreign values), which blocks DNS-rebinding attacks from web pages,
but it still has no authentication — anyone who can connect can run code.

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
- `mcp_jupyter_endpoint` - Serve the authenticated endpoint on the Jupyter port (default: True)
- `mcp_endpoint_path` - Endpoint path relative to Jupyter's base_url (default: "mcp")
- `mcp_standalone` - Also run the separate, **unauthenticated** listener (default: False)
- `mcp_port` - Port of the standalone listener (default: 3001; only used with `mcp_standalone`)
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
c.IdentityProvider.token = "change-me"  # also authenticates MCP clients
```

### Full Configuration

```python
c = get_config()

# MCP Server Configuration
c.MCPExtensionApp.mcp_name = "Advanced Jupyter MCP Server"
c.MCPExtensionApp.mcp_endpoint_path = "mcp"  # served at {base_url}mcp
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
│   ├── mcp_handler.py     # Authenticated {base_url}mcp endpoint
│   ├── asgi_bridge.py     # Runs the FastMCP app inside Tornado
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
