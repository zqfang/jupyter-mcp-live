# Jupyter MCP Executor - Project Overview

**Last Updated**: 2025-11-09
**Version**: 0.2.x
**Status**: Production Ready - Recently Renamed & Refactored

---

## 📋 Table of Contents

1. [Project Purpose](#project-purpose)
2. [Architecture Overview](#architecture-overview)
3. [Module Structure](#module-structure)
4. [Key Features](#key-features)
5. [MCP Interface](#mcp-interface)
6. [Installation & Setup](#installation--setup)
7. [Development Guide](#development-guide)
8. [Testing](#testing)
9. [Recent Refactoring](#recent-refactoring)
10. [Troubleshooting](#troubleshooting)

---

## Project Purpose

**Jupyter Server MCP** is a Model Context Protocol (MCP) server extension for Jupyter Server that enables AI assistants (like Claude) to interact with Jupyter notebooks programmatically.

### What It Does

- **Execute Code**: Run code in Jupyter kernels and get results
- **Manage Notebooks**: Create, read, update, delete notebook cells
- **Kernel Management**: Start, stop, switch between different kernels (Python, R, Julia, etc.)
- **Collaborative Editing**: Support for YDoc when notebooks are open in JupyterLab
- **MCP Integration**: Expose all functionality through MCP tools for AI assistants

### Use Cases

- AI-assisted data analysis and visualization
- Automated notebook creation and modification
- Code execution with immediate feedback
- Multi-kernel notebook workflows
- Collaborative AI-human notebook editing

---

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│              MCP Client (Claude, etc.)              │
└────────────────────┬────────────────────────────────┘
                     │ MCP Protocol (HTTP/SSE)
┌────────────────────▼────────────────────────────────┐
│           FastMCP Server (mcp_server.py)            │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│      notebook_execution_tools.py (MCP Interface)    │
│  ┌──────────────────────────────────────────────┐  │
│  │  • list_notebook_kernels()                   │  │
│  │  • list_notebook_sessions()                  │  │
│  │  • setup_notebook()                          │  │
│  │  • execute_notebook_code()                   │  │
│  │  • modify_notebook_cells()                   │  │
│  │  • query_notebook()                          │  │
│  │  • read_cell()                               │  │
│  │  • switch_notebook_kernel()                  │  │
│  │  • shutdown_notebook()                       │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│              Service Layer (New!)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │  KernelManager    │  ExecutionEngine         │  │
│  │  CellOperations   │  QueryService            │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│              Adapter Layer (Dual-Path)              │
│  ┌──────────────────────────────────────────────┐  │
│  │  YDocAdapter        │  NbformatAdapter       │  │
│  │  (Collaborative)    │  (File-based)          │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│           Jupyter Server Infrastructure             │
│  ┌──────────────────────────────────────────────┐  │
│  │  • KernelManager                             │  │
│  │  • YDoc (jupyter_ydoc)                       │  │
│  │  • Notebook files (.ipynb)                   │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### Design Patterns

1. **Adapter Pattern**: Transparent switching between YDoc (collaborative) and nbformat (file-based) operations
2. **Service Layer**: Business logic separated into focused services
3. **Singleton Pattern**: Services initialized once and reused
4. **Factory Pattern**: `get_notebook_adapter()` creates appropriate adapter
5. **Strategy Pattern**: Different execution strategies for open vs closed notebooks

---

## Module Structure

### Project Layout

```
jupyter-mcp-executor/
├── jupyter_mcp_executor/
│   ├── __init__.py
│   ├── extension.py                  # Jupyter server extension
│   ├── mcp_server.py                 # FastMCP server
│   │
│   ├── notebook_execution_tools.py   # MCP interface (407 lines, was 2,333)
│   │
│   ├── Foundation Layer
│   │   ├── notebook_config.py        # Constants, enums, config
│   │   ├── notebook_models.py        # Data classes
│   │   ├── server_context.py         # Server context management
│   │   ├── path_utils.py             # Path normalization
│   │   ├── validation.py             # Validation utilities
│   │   ├── kernel_tracker.py         # Kernel tracking
│   │   └── kernel_utils.py           # Kernel detection & language resolution
│   │
│   ├── I/O & Conversion
│   │   ├── notebook_io.py            # File I/O
│   │   ├── output_filter.py          # Output filtering
│   │   └── output_converter.py       # Format conversion
│   │
│   ├── YDoc & Collaborative Editing
│   │   ├── ydoc_helpers.py           # YDoc utilities
│   │   ├── notebook_adapter.py       # Adapter protocol
│   │   ├── ydoc_adapter.py           # YDoc implementation
│   │   └── nbformat_adapter.py       # Nbformat implementation
│   │
│   ├── Execution Layer
│   │   └── execution_utils.py        # Kernel execution primitives
│   │
│   └── Service Layer
│       ├── execution_engine.py       # Code execution & output updates
│       ├── kernel_manager.py         # Kernel lifecycle
│       ├── cell_operations.py        # Cell manipulation
│       └── query_service.py          # Read-only queries
│
├── tests/
│   ├── test_notebook_execution_tools.py  # Main test suite (46 tests)
│   ├── test_refactored_imports.py        # Import validation (10 tests)
│   └── test_kernel_utils.py              # Kernel utilities tests (29 tests)
│
├── examples/
│   └── jupyter_config_with_execution_tools.py
│
└── Documentation
    ├── README.md
    ├── CLAUDE.md                     # This file
    ├── REFACTORING_PLAN.md           # Refactoring documentation
    ├── REFACTORING_COMPLETE.md       # Refactoring summary
    ├── COLLABORATIVE_EDITING.md      # YDoc integration guide
    └── YDOC_FIX_IMPLEMENTED.md       # Bug fix documentation
```

### Module Responsibilities

#### **notebook_execution_tools.py** (Main Interface)
- **Purpose**: MCP tool registration and public API
- **Size**: 407 lines (82.5% reduction from original 2,333)
- **Exports**: 9 MCP functions
- **Dependencies**: All service modules

#### **Service Layer Modules**

**kernel_manager.py** (335 lines)
- Kernel lifecycle management
- Notebook setup/creation
- Kernel switching
- Shutdown operations

**execution_engine.py** (270 lines)
- Code execution in kernels
- Output collection and filtering
- Cell output updates (YDoc-aware)
- Package installation

**cell_operations.py** (230 lines)
- Add/edit/delete code cells
- Add/edit markdown cells
- Automatic execution for code cells
- Persistence management

**query_service.py** (245 lines)
- View notebook source
- Check server status
- List sessions
- Find cells by various identifiers

#### **Adapter Layer**

**notebook_adapter.py** (165 lines)
- Abstract adapter protocol (ABC)
- Factory function for adapter selection
- 10 abstract methods defining interface

**ydoc_adapter.py** (185 lines)
- YDoc-based collaborative editing
- In-memory operations
- Real-time updates
- Supports open notebooks in JupyterLab

**nbformat_adapter.py** (200 lines)
- File-based editing
- Disk persistence
- Supports closed notebooks
- Atomic read-modify-write

#### **Foundation Modules**

**notebook_config.py** (70 lines)
- Configuration constants
- Enums (KernelLanguage, etc.)
- Type literals (QueryType, CellOperation, etc.)
- No magic numbers/strings

**notebook_models.py** (100 lines)
- CellIdentifier (position, UUID, execution count)
- ExecutionResult
- CellContent
- KernelInfo
- NotebookInfo

**output_filter.py** (140 lines)
- Filters large binary data (images, HTML)
- Reduces token usage in MCP responses
- Configurable thresholds
- Placeholder generation

**kernel_utils.py** (230 lines) **NEW!**
- Smart kernel name resolution
- Language detection from kernel specs
- Pattern-based language detection (fallback)
- Find kernels by language
- Support for custom kernel names (rapids, r45, etc.)
- AI agent-friendly kernel resolution

---

## Key Features

### 1. **Dual-Path Architecture** (YDoc + nbformat)

The adapter pattern enables transparent switching:

```python
# Automatically chooses correct adapter
adapter = await get_notebook_adapter(notebook_path)

# If notebook is open in JupyterLab → YDocAdapter
#   - Real-time collaborative editing
#   - Immediate UI updates
#   - In-memory operations

# If notebook is closed → NbformatAdapter
#   - File-based editing
#   - Disk persistence
#   - Atomic operations
```

**Why This Matters**:
- Prevents cell deletion bugs when notebooks are open
- Enables collaborative AI-human editing
- Maintains backwards compatibility
- Performance optimized for each case

### 2. **YDoc Integration** (Collaborative Editing)

**File ID → Room ID → YNotebook Flow**:
```python
file_id = await get_file_id(notebook_path)
room_id = f"json:notebook:{file_id}"
yroom = await get_ydoc_room(room_id)
ydoc = yroom.get_jupyter_ydoc()  # YNotebook instance
```

**Critical Bug Fix** (Implemented):
- **Problem**: Cells deleted after execution when notebook was open
- **Root Cause**: Disk writes overwrote YDoc changes
- **Solution**: Update YDoc first, then persist to disk
- **Status**: ✅ Fixed and tested

**Output Persistence Strategy**:
```python
if adapter.is_collaborative:
    # 1. Update YDoc (immediate UI feedback)
    await adapter.update_cell_outputs(position, exec_count, outputs)

    # 2. Persist to disk (permanent storage)
    with NotebookContext(notebook_path) as nb:
        nb.cells[position].outputs = outputs
```

### 3. **Multi-Kernel Support**

Supports multiple kernel languages with intelligent detection:
- Python (python3, python3.11, rapids, etc.)
- R (ir, irkernel, r, r45, etc.)
- Julia (julia-1.9, julia-1.10, etc.)
- JavaScript (and others via Jupyter kernel specs)

**Smart Kernel Resolution** (New!):
```python
from jupyter_mcp_executor.kernel_utils import resolve_kernel_name

# Handles exact match, language match, and custom kernels
kernel_name = resolve_kernel_name("rapids", kernelspecs)  # → "rapids"
kernel_name = resolve_kernel_name("r", kernelspecs)       # → "r45" (if available)
kernel_name = resolve_kernel_name("python", kernelspecs)  # → "python3"
```

**Kernel Language Detection**:
```python
from jupyter_mcp_executor.kernel_utils import detect_kernel_language_from_spec, get_kernel_info

# Detect from kernel spec
spec = {"spec": {"language": "python", "display_name": "RAPIDS"}}
lang = detect_kernel_language_from_spec(spec)  # → "python"

# Get full kernel info
info = get_kernel_info("rapids", kernelspecs)
# → {"language": "python", "display_name": "RAPIDS (Python)"}
```

**Key Features**:
- **Custom Kernel Support**: Works with any kernel name (rapids, r45, etc.)
- **Language-Based Resolution**: Request "R" and get best available R kernel
- **Pattern Detection**: Detects language from kernel names (r45 → R, python3 → python)
- **Spec Querying**: Reads actual kernel specs at runtime (no hardcoded mappings)
- **AI-Friendly**: Perfect for AI agents creating notebooks with custom kernels

**Kernel Tracking**:
```python
# Each notebook maintains its own kernel session
tracker = get_kernel_tracker()
tracker.track(notebook_path, kernel_id)
kernel_id = tracker.get(notebook_path)
```

### 4. **Output Filtering** (Token Efficiency)

Automatically filters large outputs to reduce MCP token usage:

```python
# Configuration
FILTER_MIME_TYPES = ("image/png", "image/jpeg", "image/svg+xml", "text/html")
FILTER_THRESHOLD = 1000  # bytes

# Large images/HTML replaced with:
"[Filtered image/png, image/jpeg - available in notebook]"
```

### 5. **Type Safety** (Python 3.10+)

All data structures use dataclasses:

```python
@dataclass
class CellIdentifier:
    execution_count: int | None = None
    position_index: int | None = None
    cell_id: str | None = None

    def __post_init__(self):
        # Validates exactly one identifier provided
        ...

@dataclass
class ExecutionResult:
    execution_count: int | None
    outputs: list[dict[str, Any]]
    status: str
    kernel_id: str
    started_new_kernel: bool = False
    error: str | None = None
```

---

## MCP Interface

### MCP-Exported Functions

All functions in `notebook_execution_tools.py` are exposed as MCP tools:

#### 1. **list_notebook_kernels()**

```python
Returns: {"kernelspecs": {kernel_name: spec_dict}}
```

#### 2. **list_notebook_sessions()**

```python
Returns: [
    {
        "id": str,
        "path": str,
        "name": str,
        "type": str,
        "kernel": {
            "id": str,
            "name": str,
            "execution_state": str,
            "connections": int
        }
    }
]
```

#### 3. **setup_notebook(notebook_path, kernel_name="python3")**

```python
# Creates notebook if doesn't exist, starts kernel
Returns: {
    "path": str,
    "exists": bool,
    "kernel_name": str,
    "kernel_id": str
}
```

#### 4. **execute_notebook_code(notebook_path, execution_type, ...)**

```python
# execution_type: "execute_cell" | "install_packages"

# For execute_cell:
execute_notebook_code(path, "execute_cell", position_index=0)
Returns: {
    "status": "ok" | "error",
    "execution_count": int,
    "outputs": [...],
    "kernel_id": str
}

# For install_packages:
execute_notebook_code(path, "install_packages", package_names="numpy pandas")
Returns: str (installation message)
```

#### 5. **modify_notebook_cells(notebook_path, operation, cell_content, ...)**

```python
# operation: "add_code" | "edit_code" | "add_markdown" |
#            "edit_markdown" | "delete"

# Add code cell with execution:
modify_notebook_cells(path, "add_code", "x = 42", execute=True)
Returns: {
    "status": "ok",
    "execution_count": 1,
    "outputs": [...],
    "position": int
}

# Add markdown cell:
modify_notebook_cells(path, "add_markdown", "# Title")
Returns: {"message": str, "position": int}

# Delete cell:
modify_notebook_cells(path, "delete", position_index=0)
Returns: {"message": str}
```

#### 6. **query_notebook(notebook_path, query_type, ...)**

```python
# query_type: "view_source" | "check_server" | "list_sessions" |
#             "list_kernels" | "get_position_index"

# View all cells:
query_notebook(path, "view_source")
Returns: [{"cell_type": str, "source": str, ...}, ...]

# View specific cell by position:
query_notebook(path, "view_source", position_index=0)
Returns: {"cell_type": str, "source": str, "position": int, ...}

# View by execution count:
query_notebook(path, "view_source", execution_count=5)
Returns: {"cell_type": str, "source": str, "execution_count": 5, ...}
```

#### 7. **read_cell(notebook_path, position_index/cell_id/execution_count)**

```python
# Convenience function - simpler than query_notebook

read_cell(path, position_index=0)
read_cell(path, cell_id="abc-123-...")
read_cell(path, execution_count=5)

Returns: {
    "cell_type": str,
    "source": str,
    "position": int,
    "execution_count": int | None,
    "outputs": [...],
    "id": str
}
```

#### 8. **switch_notebook_kernel(notebook_path, kernel_name)**

```python
# Switches kernel and updates notebook metadata
Returns: {
    "kernel_name": str,
    "kernel_id": str,
    "previous_kernel_id": str
}
```

#### 9. **shutdown_notebook(notebook_path, shutdown_kernel=True)**

```python
Returns: {
    "message": str,
    "kernel_id": str | None
}
```

### MCP Tool Registration

Tools are registered in `mcp_server.py`:

```python
mcp.tool()(notebook_execution_tools.list_notebook_kernels)
mcp.tool()(notebook_execution_tools.list_notebook_sessions)
mcp.tool()(notebook_execution_tools.setup_notebook)
mcp.tool()(notebook_execution_tools.execute_notebook_code)
mcp.tool()(notebook_execution_tools.modify_notebook_cells)
mcp.tool()(notebook_execution_tools.query_notebook)
mcp.tool()(notebook_execution_tools.read_cell)
mcp.tool()(notebook_execution_tools.switch_notebook_kernel)
mcp.tool()(notebook_execution_tools.shutdown_notebook)
```

---

## Installation & Setup

### Requirements

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    "jupyter_server>=1.6,<3",
    "fastmcp>=2.0.0",
    "nbformat>=5.0",
    "jupyter-collaboration",  # For YDoc support
    "jupyter_ydoc>=2.0.0",
    "pycrdt>=0.8.0"
]
```

### Installation

```bash
# Development install
pip install -e .

# Or with uv
uv pip install -e .
```

### Jupyter Server Configuration

Enable the extension in `jupyter_server_config.py`:

```python
c.ServerApp.jpserver_extensions = {
    "jupyter_mcp_executor": True
}
```

Or use the example config:

```python
# examples/jupyter_config_with_execution_tools.py
c.MCP.tools = [
    "jupyter_mcp_executor.notebook_execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.notebook_execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.notebook_execution_tools:execute_notebook_code",
    "jupyter_mcp_executor.notebook_execution_tools:setup_notebook",
    "jupyter_mcp_executor.notebook_execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.notebook_execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.notebook_execution_tools:modify_notebook_cells",
    "jupyter_mcp_executor.notebook_execution_tools:query_notebook",
]
```

### Starting the Server

```bash
# Start Jupyter server with MCP
jupyter server --config examples/jupyter_config_with_execution_tools.py

# MCP server will be available at:
# http://localhost:8888/mcp (or configured port)
```

---

## Development Guide

### Project Setup

```bash
# Clone repository
git clone <repo-url>
cd jupyter-mcp-executor

# Create virtual environment
python3.10 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Or with uv
uv pip install -e ".[dev]"
```

### Code Structure Best Practices

**1. Use the Adapter Pattern**:
```python
# Always use the adapter factory
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter

async def my_operation(notebook_path: str):
    adapter = await get_notebook_adapter(notebook_path)
    # adapter handles YDoc vs nbformat automatically
    await adapter.add_cell(cell_content)
```

**2. Use Data Models**:
```python
from jupyter_mcp_executor.notebook_models import CellIdentifier, CellContent

# Type-safe identifiers
identifier = CellIdentifier(position_index=0)

# Type-safe content
content = CellContent(
    source="print('hello')",
    cell_type="code"
)
```

**3. Use Configuration Constants**:
```python
from jupyter_mcp_executor.notebook_config import EXECUTION_CONFIG

timeout = EXECUTION_CONFIG.default_timeout  # Not magic number!
```

**4. Filter Outputs**:
```python
from jupyter_mcp_executor.output_filter import OutputFilter

filtered = OutputFilter.filter_outputs(outputs)
```

**5. Use Kernel Utilities for Custom Kernels**:
```python
from jupyter_mcp_executor.kernel_utils import resolve_kernel_name, get_kernel_info

# AI agent wants to create notebook with custom kernel
async def create_notebook_with_kernel(path: str, requested_kernel: str):
    # Get available kernels
    kernels = await list_notebook_kernels()
    kernelspecs = kernels["kernelspecs"]

    # Resolve kernel name (handles "rapids", "r45", or language names like "R")
    kernel_name = resolve_kernel_name(requested_kernel, kernelspecs)

    # Get kernel info for metadata
    info = get_kernel_info(kernel_name, kernelspecs)

    # Create notebook with resolved kernel
    return await setup_notebook(path, kernel_name)
```

### Adding a New MCP Tool

1. **Create the service method** (in appropriate service module)
2. **Add to `notebook_execution_tools.py`** (public interface)
3. **Register in `mcp_server.py`**
4. **Add tests** in `test_notebook_execution_tools.py`
5. **Update documentation**

Example:

```python
# 1. In query_service.py
async def get_notebook_metadata(self, notebook_path: str) -> dict:
    adapter = await get_notebook_adapter(notebook_path)
    # Implementation...

# 2. In notebook_execution_tools.py
async def get_notebook_metadata(notebook_path: str) -> dict:
    """Get notebook metadata."""
    return await _query_service.get_notebook_metadata(notebook_path)

# 3. In mcp_server.py
mcp.tool()(notebook_execution_tools.get_notebook_metadata)

# 4. In test_notebook_execution_tools.py
@pytest.mark.asyncio
async def test_get_notebook_metadata(temp_notebook):
    result = await get_notebook_metadata(temp_notebook)
    assert "metadata" in result
```

### Debugging

**Enable Debug Logging**:
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Or for specific modules
logging.getLogger("jupyter_mcp_executor.execution_engine").setLevel(logging.DEBUG)
```

**Check YDoc Path**:
```python
from jupyter_mcp_executor.ydoc_helpers import get_ynotebook

ydoc = await get_ynotebook(notebook_path)
if ydoc:
    print("Using YDoc (collaborative)")
else:
    print("Using nbformat (file-based)")
```

**Inspect Kernel Tracker**:
```python
from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker

tracker = get_kernel_tracker()
print(tracker.get_all_tracked())
```

---

## Testing

### Test Suite

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_notebook_execution_tools.py -v

# Run import validation
pytest tests/test_refactored_imports.py -v

# Run with coverage
pytest --cov=jupyter_mcp_executor --cov-report=html
```

### Test Structure

**Main Test File**: `test_notebook_execution_tools.py` (46 tests)
- `TestHelperFunctions` (9 tests) - Utilities
- `TestServerContext` (2 tests) - Context management
- `TestListFunctions` (3 tests) - Listing operations
- `TestExecuteNotebookCode` (4 tests) - Execution
- `TestSetupNotebook` (3 tests) - Notebook setup
- `TestSwitchNotebookKernel` (1 test) - Kernel switching
- `TestShutdownNotebook` (2 tests) - Shutdown
- `TestModifyNotebookCells` (8 tests) - Cell operations
- `TestQueryNotebook` (7 tests) - Query operations
- `TestReadCell` (4 tests) - Read operations
- `test_output_filtering` (1 test) - Output filtering
- `TestIntegration` (1 test) - Complete workflow

**Import Validation**: `test_refactored_imports.py` (10 tests)
- Validates all module imports
- Tests data model validation
- Checks configuration access

### Mock Objects

Tests use comprehensive mocks:
- `MockKernelManager` - Kernel lifecycle
- `MockKernel` - Kernel instances
- `MockKernelClient` - Kernel communication
- `MockKernelSpecManager` - Kernel specs
- `MockServerApp` - Jupyter server
- `MockSessionManager` - Session management

### Writing Tests

**Test Pattern**:
```python
@pytest.mark.asyncio
async def test_my_feature(temp_notebook, mock_serverapp):
    """Test description."""
    # Setup
    set_server_context(mock_serverapp)

    # Execute
    result = await my_function(temp_notebook)

    # Assert
    assert result["status"] == "ok"

    # Verify side effects
    nb = read_notebook(temp_notebook)
    assert len(nb.cells) == 1
```

---

## Recent Refactoring

### Major Refactoring (2025-11-02)

**Scope**: Complete architectural overhaul

**Metrics**:
- Main file: 2,333 → 407 lines (82.5% reduction)
- Created: 18 new focused modules
- Eliminated: ~300 lines of duplicate code
- Tests: All 46 passing ✅

**Key Changes**:

1. **Adapter Pattern** - Eliminated YDoc/nbformat duplication
2. **Service Layer** - Separated business logic
3. **Data Models** - Type-safe dataclasses
4. **Configuration** - Centralized constants
5. **Output Filtering** - Consolidated logic

**Documentation**:
- [REFACTORING_PLAN.md](REFACTORING_PLAN.md) - Detailed plan
- [REFACTORING_COMPLETE.md](REFACTORING_COMPLETE.md) - Summary
- [COLLABORATIVE_EDITING.md](COLLABORATIVE_EDITING.md) - YDoc guide
- [YDOC_FIX_IMPLEMENTED.md](YDOC_FIX_IMPLEMENTED.md) - Bug fix

**Backwards Compatibility**: 100% ✅
- All MCP functions unchanged
- All existing code works identically
- No breaking changes

---

## Troubleshooting

### Common Issues

#### 1. **Cells Disappear After Execution**

**Symptom**: Cells added/executed disappear when notebook is open in JupyterLab

**Cause**: Disk writes overwriting YDoc changes

**Status**: ✅ Fixed (see [YDOC_FIX_IMPLEMENTED.md](YDOC_FIX_IMPLEMENTED.md))

**Verify Fix**:
```bash
# Check logs for YDoc path
DEBUG:Using YDoc for view_source
INFO:Updating cell outputs via YDoc
DEBUG:Persisting cell outputs to disk
```

#### 2. **Server Context Not Available**

**Symptom**: `RuntimeError: server context not available`

**Solution**:
```python
from jupyter_mcp_executor.server_context import set_server_context

# In extension.py
set_server_context(serverapp)
```

#### 3. **YDoc Not Detected**

**Symptom**: Always using nbformat even when notebook is open

**Checklist**:
- Is `jupyter-collaboration` installed?
- Is notebook actually open in JupyterLab?
- Check file_id_manager exists:
  ```python
  serverapp = get_server_context()
  print(hasattr(serverapp, 'file_id_manager'))
  ```

#### 4. **Import Errors After Refactoring**

**Symptom**: `ModuleNotFoundError` or `ImportError`

**Solution**: Update imports to new structure:
```python
# Old (internal)
from jupyter_mcp_executor.notebook_execution_tools import _normalize_path

# New (public)
from jupyter_mcp_executor.path_utils import normalize_path
```

**Verification**:
```bash
pytest tests/test_refactored_imports.py -v
```

#### 5. **Tests Failing**

**Common Causes**:
- Server context not reset between tests
- Kernel tracker not cleared
- Incorrect mock setup

**Fix**:
```python
# In test file
@pytest.fixture(autouse=True)
def reset_context():
    from jupyter_mcp_executor.server_context import _server_context
    from jupyter_mcp_executor.kernel_tracker import get_kernel_tracker

    _server_context["serverapp"] = None
    get_kernel_tracker()._kernels = {}

    yield

    _server_context["serverapp"] = None
    get_kernel_tracker()._kernels = {}
```

### Debug Workflows

**Check Adapter Selection**:
```python
from jupyter_mcp_executor.notebook_adapter import get_notebook_adapter

adapter = await get_notebook_adapter(notebook_path)
print(f"Using: {adapter.__class__.__name__}")
print(f"Collaborative: {adapter.is_collaborative}")
```

**Inspect Execution Flow**:
```python
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s'
)
```

**Verify Output Persistence**:
```bash
# After execution, check both:
# 1. JupyterLab UI (should show outputs immediately)
# 2. .ipynb file on disk (should have outputs persisted)

cat notebook.ipynb | jq '.cells[0].outputs'
```

---

## API Reference Quick Links

### Core Services
- **KernelManager**: [kernel_manager.py](jupyter_mcp_executor/kernel_manager.py)
- **ExecutionEngine**: [execution_engine.py](jupyter_mcp_executor/execution_engine.py)
- **CellOperations**: [cell_operations.py](jupyter_mcp_executor/cell_operations.py)
- **QueryService**: [query_service.py](jupyter_mcp_executor/query_service.py)

### Adapters
- **NotebookAdapter**: [notebook_adapter.py](jupyter_mcp_executor/notebook_adapter.py)
- **YDocAdapter**: [ydoc_adapter.py](jupyter_mcp_executor/ydoc_adapter.py)
- **NbformatAdapter**: [nbformat_adapter.py](jupyter_mcp_executor/nbformat_adapter.py)

### Utilities
- **Output Filter**: [output_filter.py](jupyter_mcp_executor/output_filter.py)
- **Path Utils**: [path_utils.py](jupyter_mcp_executor/path_utils.py)
- **Validation**: [validation.py](jupyter_mcp_executor/validation.py)
- **Kernel Utils**: [kernel_utils.py](jupyter_mcp_executor/kernel_utils.py) **NEW!**

---

## Contributing

### Coding Standards

- **Python Version**: 3.10+
- **Type Hints**: Required everywhere
- **Import Style**: Absolute imports
- **Docstrings**: Google style
- **Line Length**: 100 characters
- **Formatting**: Black-compatible

### Pull Request Process

1. Create feature branch from `main`
2. Write tests for new functionality
3. Ensure all tests pass (`pytest`)
4. Update documentation
5. Submit PR with clear description

### Commit Message Format

```
<type>: <subject>

<body>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

---

## Resources

### Documentation
- [README.md](README.md) - Project overview
- [REFACTORING_PLAN.md](REFACTORING_PLAN.md) - Refactoring plan
- [REFACTORING_COMPLETE.md](REFACTORING_COMPLETE.md) - Refactoring summary
- [COLLABORATIVE_EDITING.md](COLLABORATIVE_EDITING.md) - YDoc guide
- [YDOC_FIX_IMPLEMENTED.md](YDOC_FIX_IMPLEMENTED.md) - Bug fix docs

### External Links
- [Jupyter Server](https://jupyter-server.readthedocs.io/)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [jupyter_ydoc](https://github.com/jupyter-server/jupyter_ydoc)
- [MCP Protocol](https://modelcontextprotocol.io/)

---

## Version History

### v0.1.x (Current)
- ✅ Complete refactoring (2025-11-02)
- ✅ Modular architecture
- ✅ YDoc collaborative editing
- ✅ Output persistence fix
- ✅ Comprehensive test suite
- ✅ 100% backwards compatible

### Future Plans
- Extended YDoc testing
- Performance optimization
- Additional MCP tools
- Enhanced error handling
- Plugin system

---

**Maintained By**: Project contributors
**License**: See LICENSE file
**Status**: Production Ready ✅

*Last Updated: 2025-11-02*
