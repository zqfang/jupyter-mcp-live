# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**Project**: Jupyter MCP Executor
**Version**: 0.1.0
**Python**: 3.10+
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
# Install in development mode
pip install -e ".[dev]"

# Or with uv (faster)
uv pip install -e ".[dev]"
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_notebook_execution_tools.py -v

# Run single test
pytest tests/test_notebook_execution_tools.py::TestExecuteNotebookCode::test_execute_cell -v

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

# MCP server runs on http://localhost:8080/mcp (default port 3001)
```

## Architecture Overview

### Layered Architecture

```text
MCP Client (Claude/Gemini)
    ↓
FastMCP Server (mcp_server.py)
    ↓
MCP Tools Interface (notebook_execution_tools.py)
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
- `notebook_execution_tools.py` - Public MCP interface (9 exported functions)

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
- `ydoc_helpers.py` - YDoc utility functions

### Foundation/Utilities

- `notebook_models.py` - Dataclasses (CellIdentifier, ExecutionResult, etc.)
- `notebook_config.py` - Configuration constants and enums
- `kernel_utils.py` - Kernel detection and language resolution
- `kernel_tracker.py` - Tracks kernel sessions per notebook
- `output_filter.py` - Filter large outputs for token efficiency
- `output_converter.py` - Output format conversion
- `notebook_io.py` - File I/O operations
- `path_utils.py` - Path normalization utilities
- `validation.py` - Input validation utilities
- `server_context.py` - Server context management
- `exceptions.py` - Custom exception classes
- `execution_utils.py` - Low-level kernel execution primitives
- `persistence_utils.py` - Disk persistence helpers (used for nbformat adapter and execution output writes)

## MCP Functions (Public API)

All functions in `notebook_execution_tools.py`:

1. **list_notebook_kernels()** - Get available kernel specs
2. **list_notebook_sessions()** - List active kernel sessions
3. **setup_notebook(path, kernel_name)** - Create notebook and start kernel
4. **execute_notebook_code(path, execution_type, ...)** - Execute cell or install packages
5. **modify_notebook_cells(path, operation, ...)** - Add/edit/delete cells
6. **query_notebook(path, query_type, ...)** - Read notebook/cells
7. **read_cell(path, position_index/cell_id/execution_count)** - Read specific cell
8. **switch_notebook_kernel(path, kernel_name)** - Switch to different kernel
9. **shutdown_notebook(path, shutdown_kernel)** - Shutdown kernel session

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
2. Add public function to `notebook_execution_tools.py`
3. Register in `mcp_server.py` via `mcp.tool()(function_name)`
4. Add tests to `test_notebook_execution_tools.py`
5. Update this documentation

Example:

```python
# 1. In query_service.py
async def get_notebook_metadata(self, notebook_path: str) -> dict:
    adapter = await get_notebook_adapter(notebook_path)
    return await adapter.get_metadata()

# 2. In notebook_execution_tools.py
async def get_notebook_metadata(notebook_path: str) -> dict:
    """Get notebook metadata."""
    return await _query_service.get_notebook_metadata(notebook_path)

# 3. In extension.py, register the tool
# 4. Add test in test_notebook_execution_tools.py
```

### Test Structure

- `tests/test_notebook_execution_tools.py` - Main test suite (46 tests)
- `tests/test_imports.py` - Import validation tests
- `tests/test_kernel_utils.py` - Kernel utilities tests (29 tests)
- `tests/test_mcp_server.py` - MCP server tests
- `tests/test_extension.py` - Extension tests
- `tests/conftest.py` - Shared fixtures and mocks

All tests use comprehensive mocks (no actual kernels/servers started).

### Common Debugging

**Enable debug logging**:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

**Check which adapter is being used**:

```python
from jupyter_mcp_executor.ydoc_helpers import get_ynotebook

ydoc = await get_ynotebook(notebook_path)
if ydoc:
    print("Using YDoc (collaborative)")
else:
    print("Using nbformat (file-based)")
```

**Inspect tracked kernels**:

```python
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker

tracker = get_kernel_tracker()
print(tracker.get_all_tracked())
```

## Important Behaviors

### YDoc vs Nbformat Selection

The adapter is selected automatically in `get_notebook_adapter()`:

- If notebook is open in JupyterLab → `YDocAdapter`
- Otherwise → `NbformatAdapter`

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
- [jupyter_mcp_executor/notebook_execution_tools.py](jupyter_mcp_executor/notebook_execution_tools.py) - Public API
- [jupyter_mcp_executor/notebook_adapter.py](jupyter_mcp_executor/notebook_adapter.py) - Adapter pattern
- [tests/conftest.py](tests/conftest.py) - Test fixtures and mocks
