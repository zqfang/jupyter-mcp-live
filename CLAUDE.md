# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**Project**: Jupyter MCP Executor
**Version**: 0.3.0
**Python**: 3.11+
**Status**: Production Ready

## What This Project Does

A Model Context Protocol (MCP) server extension for Jupyter Server that enables AI assistants to programmatically interact with Jupyter notebooks:

- Execute code in any Jupyter kernel (Python, R, Julia, etc.)
- Create, read, update, delete notebook cells
- Manage kernel lifecycle (start, stop, switch kernels)
- Support collaborative editing (YDoc integration for open notebooks)
- Filter outputs to optimize token usage

## Essential Commands

### Development Setup

```bash
# Install in development mode with test dependencies (pytest etc.)
pip install -e ".[test]"

# Or with uv (faster)
uv pip install -e ".[test]"
```

Note: the `[dev]` extra only contains ruff — use `[test]` to get pytest and plugins.

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_notebook_execution_tools.py -v

# Run single test
pytest tests/test_notebook_execution_tools.py::TestExecuteCell::test_execute_cell -v

# Run with coverage
pytest --cov=jupyter_mcp_executor --cov-report=html

# Run specific test pattern
pytest -k "test_execute" -v
```

### Code Quality

```bash
# Run ruff linter
ruff check jupyter_mcp_executor/

# Run ruff formatter
ruff format jupyter_mcp_executor/

# Auto-fix issues
ruff check --fix jupyter_mcp_executor/
```

### Running the Server

```bash
# Start Jupyter with MCP extension
jupyter lab --config examples/jupyter_config_with_execution_tools.py

# MCP server runs at http://localhost:<mcp_port>/mcp
# Default port is 3001; the example config sets c.MCPExtensionApp.mcp_port = 8080
```

## Architecture Overview

### Layered Architecture

```text
MCP Client (Claude/Gemini)
    ↓
FastMCP Server (mcp_server.py)
    ↓
MCP Tools Interface (execution_tools.py)
    ↓
Service Layer (kernel_manager, execution_engine, cell_operations, query_service)
    ↓
Adapter Layer (ydoc_adapter vs nbformat_adapter - auto-selected)
    ↓
Jupyter Server Infrastructure (kernels, YDoc, .ipynb files)
```

### Key Design Patterns

**1. Adapter Pattern** - Automatically switches between:

- `YDocAdapter`: For notebooks open in JupyterLab (real-time collaborative editing)
- `NbformatAdapter`: For closed notebooks (file-based editing)

**2. Service Layer** - Business logic separated into focused services:

- `KernelManager`: Kernel lifecycle (start, stop, switch)
- `ExecutionEngine`: Code execution and output handling
- `CellOperations`: Cell manipulation (add, edit, delete)
- `QueryService`: Read-only notebook queries

**3. Singleton Services** - Services initialized once via `service_container.py`

### Critical Architecture Details

**YDoc Collaborative Editing**:
When a notebook is open in JupyterLab, all changes go through YDoc only:

```python
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter

# Auto-selects YDocAdapter or NbformatAdapter
adapter = await get_notebook_adapter(notebook_path)
await adapter.add_cell(cell_content)  # Updates YDoc; JupyterLab autosave handles disk
```

**Why no manual disk writes in collaborative mode?**
Writing directly to the .ipynb file while a notebook is open causes JupyterLab to reconcile the stale file content against the live YDoc state, triggering a full reload and scroll-to-top. JupyterLab autosave handles disk persistence automatically.

**Kernel Resolution**:
Supports custom kernel names via intelligent resolution:

```python
from jupyter_mcp_executor.kernel_utils import resolve_kernel_name

# Handles exact matches, language matches, and custom kernels
kernel_name = resolve_kernel_name("rapids", kernelspecs)  # → "rapids"
kernel_name = resolve_kernel_name("R", kernelspecs)       # → "r45" or "ir"
```

**Output Filtering**:
Large outputs (images, HTML >1KB) are automatically filtered to reduce token usage in MCP responses.

## Module Structure

### Core Modules (Entry Points)

- `extension.py` - Jupyter Server extension (`MCPExtensionApp`)
- `mcp_server.py` - FastMCP server wrapper (`MCPServer`)
- `execution_tools.py` - Public MCP interface (13 tool functions, decorated with `@collaborative_tool`)

### Service Layer

- `kernel_manager.py` - Kernel lifecycle management
- `execution_engine.py` - Code execution and output collection
- `cell_operations.py` - Cell manipulation operations
- `query_service.py` - Read-only notebook queries
- `service_container.py` - Service initialization and management

### Adapter Layer

- `notebook_adapter.py` - Abstract adapter protocol + factory function
- `ydoc_adapter.py` - YDoc-based (collaborative editing)
- `nbformat_adapter.py` - File-based (disk persistence)
- `ydoc_helpers.py` - YDoc utilities, including `ensure_ynotebook()` which
  recreates cleaned-up YDoc rooms to preserve CRDT history

### Foundation/Utilities

- `notebook_models.py` - Dataclasses (CellIdentifier, ExecutionResult, etc.)
- `notebook_config.py` - Configuration constants (EXECUTION_CONFIG, OUTPUT_FILTER_CONFIG)
- `kernel_utils.py` - Kernel detection and language resolution
- `kernel_tracker.py` - Tracks kernel sessions per notebook
- `output_filter.py` - Filter large outputs for token efficiency
- `output_converter.py` - Output format conversion
- `notebook_io.py` - File I/O operations
- `path_utils.py` - Path normalization utilities
- `server_context.py` - Server context management
- `execution_utils.py` - Low-level kernel execution primitives

## MCP Functions (Public API)

All functions in `execution_tools.py` (13 tools):

**Kernel/session management:**

1. **list_notebook_kernels()** - Get available kernel specs (the ONLY way to list kernels)
2. **list_notebook_sessions()** - List active kernel sessions (the ONLY way to list sessions)
3. **setup_notebook(path, kernel_name, force_new_kernel)** - Create notebook and start/reuse kernel
4. **switch_notebook_kernel(path, kernel_name)** - Switch to different kernel
5. **shutdown_notebook(path, shutdown_kernel)** - Shutdown kernel session

**Cell operations (fine-grained, one tool per operation):**

6. **add_code_cell(path, source, position_index, execute)** - Add code cell (executes by default)
7. **edit_code_cell(path, position_index, source, execute)** - Edit code cell (re-executes by default)
8. **add_markdown_cell(path, source, position_index)** - Add markdown cell
9. **edit_markdown_cell(path, position_index, source)** - Edit markdown cell
10. **delete_cell(path, position_index)** - Delete cell (later cells shift down)
11. **execute_cell(path, position_index)** - Execute an existing code cell

**Read-only queries:**

12. **view_notebook(path)** - All cells in order (list index == position_index)
13. **read_cell(path, position_index/cell_id/execution_count)** - One cell;
    the returned dict includes `position`

**Tool design rules** (the primary client path is FastMCP Code Mode):

- Each capability is exposed through exactly ONE tool. In 0.3.0 the enum-dispatched
  `query_notebook(query_type=...)` and `modify_notebook_cells(operation=...)`
  tools were dismantled: cell operations became the five cell tools, and
  `query_notebook` was replaced by `view_notebook` (its single-cell and
  get-position modes were already covered by `read_cell`, which returns
  `position`; `check_server` was dropped — a connected MCP client already knows
  the server is up). Enum-dispatched tools make tool selection ambiguous,
  produce untypeable conditionally-required parameters, and force polymorphic
  return types that hurt Code Mode codegen.
- Keep tools as small primitives with fully-typed signatures (every parameter
  unconditionally meaningful) and one predictable return shape; agents compose
  batches/loops in Code Mode scripts, so do not add batch/composite tools.

## Development Guidelines

### Type Safety

- Python 3.10+ with type hints everywhere
- Use dataclasses from `notebook_models.py`:

  ```python
  from jupyter_mcp_executor.notebook_models import CellIdentifier, CellContent

  identifier = CellIdentifier(position_index=0)
  content = CellContent(source="print('hello')", cell_type="code")
  ```

### Configuration

- Never use magic numbers/strings
- Import from `notebook_config.py`:

  ```python
  from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

  timeout = EXECUTION_CONFIG.default_timeout
  ```

### Always Use Adapters

Never read/write notebooks directly. Use the adapter factory:

```python
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter

adapter = await get_notebook_adapter(notebook_path)
# Automatically selects YDocAdapter (open notebooks) or NbformatAdapter (closed notebooks)
```

### Adding New MCP Tools

1. Add service method in appropriate service module (e.g., `query_service.py`)
2. Add public function to `execution_tools.py` (decorate with `@collaborative_tool(user=user_info)`
   and add it to `__all__`)
3. Register it: tools are loaded from the `c.MCPExtensionApp.mcp_tools` config list
   (`"module:function"` strings — see `examples/jupyter_config_with_execution_tools.py`)
   or via `jupyter_mcp_executor.tools` entrypoints
4. Add tests to `test_notebook_execution_tools.py`
5. Update this documentation

Example:

```python
# 1. In query_service.py
async def get_notebook_metadata(self, notebook_path: str) -> dict:
    adapter = await get_notebook_adapter(notebook_path)
    return await adapter.get_metadata()

# 2. In execution_tools.py
@collaborative_tool(user=user_info)
async def get_notebook_metadata(notebook_path: str) -> dict:
    """Get notebook metadata."""
    return await container.query_service.get_notebook_metadata(notebook_path)

# 3. Add "jupyter_mcp_executor.execution_tools:get_notebook_metadata" to mcp_tools config
# 4. Add test in test_notebook_execution_tools.py
```

**Docstrings are agent prompts**: FastMCP sends each function's full docstring to the
MCP client as the tool description on every request. Keep them short, state behavioral
defaults explicitly, disambiguate confusable parameters (e.g., execution_count vs
position_index), and include a recovery hint for the common failure mode.

### Test Structure

138 tests total across:

- `tests/test_notebook_execution_tools.py` - Main test suite (MCP tool functions)
- `tests/test_ydoc_helpers.py` - YDoc room recreation (`ensure_ynotebook`) tests
- `tests/test_sessions.py` - Session matching tests
- `tests/test_imports.py` - Import validation tests
- `tests/test_kernel_utils.py` - Kernel utilities tests
- `tests/test_mcp_server.py` - MCP server tests
- `tests/test_extension.py` - Extension tests
- `tests/conftest.py` - Shared fixtures and mocks (includes a `QueryService` stub
  that must be kept in sync with the real `query_service.py` routing)

All tests use comprehensive mocks (no actual kernels/servers started).

### Common Debugging

**Enable debug logging**:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

**Check which adapter is being used**:

```python
from jupyter_mcp_executor.ydoc_helpers import ensure_ynotebook, get_ynotebook

# get_ynotebook: returns the live YNotebook only if a room currently exists
# ensure_ynotebook: same, but recreates the room (from ystore) if it was
# cleaned up after the browser disconnected — this is what the adapter
# factory uses
ydoc = await ensure_ynotebook(notebook_path)
if ydoc:
    print("Using YDoc (collaborative)")
else:
    print("Using nbformat (file-based) — YDoc infra unavailable")
```

**Inspect tracked kernels**:

```python
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker

tracker = get_kernel_tracker()
print(tracker.get_all_tracked())
```

## Important Behaviors

### YDoc vs Nbformat Selection

The adapter is selected automatically in `get_notebook_adapter()` via
`ensure_ynotebook()`:

- If a YDoc room exists for the notebook → `YDocAdapter`
- If the room was cleaned up (browser disconnected for >60s) → the room is
  **recreated from the same ystore** and `YDocAdapter` is still used
- Only if YDoc infrastructure is unavailable entirely → `NbformatAdapter`

**Why recreate the room instead of falling back to NbformatAdapter?**
When the user's browser disconnects, `jupyter_server_ydoc` destroys the room
after `document_cleanup_delay` (60s). If we then wrote via `NbformatAdapter`,
the .ipynb on disk would advance while the ystore (CRDT op history) did not.
On browser reconnect, the new room detects the ystore/disk mismatch and resets
CRDT state — the browser's stored state vector no longer matches, and cells
appear **duplicated** in JupyterLab. `ensure_ynotebook()` recreates the room
with the same `file_id` (same ystore path), preserving CRDT history so
reconnecting browsers sync cleanly. Room creation is guarded by a per-room
asyncio lock in `ydoc_helpers.py` to prevent duplicate rooms under concurrency.

### Persistence Strategy

When using `YDocAdapter` (notebook is open):
- Changes are written to YDoc only; JupyterLab autosave handles disk persistence
- Direct backend disk writes while the notebook is open cause JupyterLab to reload from the stale file, so they are intentionally avoided

When using `NbformatAdapter` (notebook is closed):
- Changes are written directly to the .ipynb file on disk

### Kernel Tracking

Each notebook maintains its own kernel session via `kernel_tracker.py`. Kernels are reused across operations on the same notebook until explicitly shut down.

### Cell Identification

Cells can be identified three ways (see `CellIdentifier` in `notebook_models.py`):

- `position_index` (0-based position)
- `cell_id` (UUID)
- `execution_count` (execution number)

### Path Validation

`normalize_path()` in `path_utils.py` enforces:

- Only `.ipynb` files are accepted (prevents arbitrary file read/write through
  the notebook API)
- Relative paths must not escape the server root (traversal prevention)
- Absolute paths are allowed anywhere on the filesystem (JupyterLab supports
  notebooks outside root_dir)

All MCP tools call `normalize_path()` (directly or via the service layer)
before touching disk.

## Coding Standards

- **Python Version**: 3.10+
- **Type Hints**: Required everywhere
- **Import Style**: Absolute imports
- **Docstrings**: Google style
- **Line Length**: 88 characters (ruff default)
- **Formatting**: Use `ruff format`
- **Linting**: Use `ruff check`

## Key Files for Understanding

- [README.md](README.md) - User-facing documentation
- [pyproject.toml](pyproject.toml) - Package configuration
- [jupyter_mcp_executor/execution_tools.py](jupyter_mcp_executor/execution_tools.py) - Public API
- [jupyter_mcp_executor/notebook_adapter.py](jupyter_mcp_executor/notebook_adapter.py) - Adapter pattern
- [jupyter_mcp_executor/ydoc_helpers.py](jupyter_mcp_executor/ydoc_helpers.py) - YDoc room lifecycle
- [tests/conftest.py](tests/conftest.py) - Test fixtures and mocks
