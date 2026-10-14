# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**Project**: Jupyter MCP Executor
**Version**: 0.6.0
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

# Run with coverage (needs pytest-cov, which is not a test dependency)
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

# MCP endpoint is served by Jupyter itself at {base_url}mcp on the Jupyter port
# (example config: http://localhost:8000/mcp), authenticated with the Jupyter
# token: send "Authorization: Bearer <token>" (or "token <token>").
# The old separate listener on mcp_port is off by default (mcp_standalone).
```

## Architecture Overview

### Layered Architecture

```text
MCP Client (Claude/Gemini)
    ↓  Authorization: Bearer <Jupyter or JupyterHub token>
Jupyter Server handler at {base_url}mcp (mcp_handler.py: Jupyter auth)
    ↓  in-process ASGI bridge (asgi_bridge.py) — no extra port
FastMCP Server (mcp_server.py; Code Mode, streamable HTTP)
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
Filtering applies to MCP responses only (the notebook keeps full outputs; the
filter never mutates the original dicts):

- Large images/HTML (>1KB, `OUTPUT_FILTER_CONFIG.filter_threshold`) are
  replaced by a placeholder.
- Large plain text — stream output, error tracebacks, and `text/plain`
  results over `OUTPUT_FILTER_CONFIG.max_text_length` (4000 chars) — is
  truncated head+tail with an omission marker.

**Images for the model** (`view_cell_image`): returns a `ToolResult` whose
`structured_content` is a JSON summary and whose `content` carries the cell's
raster images as `ImageContent` (`IMAGE_CONFIG`: PNG/JPEG/GIF/WEBP, 4 per
call, 5MB each; SVG is skipped with a hint). `CellImages.from_cell()` picks
the source by cell type: code → image outputs; markdown → `attachment:`
references (looked up in `CellContent.attachments`, which both adapters
fill) and `data:` URIs, from `![](...)` and `<img src>`. Remote URLs and
local image files are reported in `skipped` (local files: not yet supported;
remote: never fetched, SSRF). Every summary entry has a `source` field
(`output`/`attachment`/`data_uri`/`url`/`file`). Code Mode would drop those
blocks — `call_tool()` hands the sandbox only structured content or text — so
`CodeModeImageForwarder` (`code_mode_images.py`, installed by `MCPServer`)
intercepts nested calls (they run through the middleware chain), moves their
images into a context-var sink, and appends them to the `execute` response
(at most `max_images_per_response`, 8). Direct, non-Code-Mode calls pass
through unchanged.

**Kernel name resolution is strict for explicit requests**:
`resolve_kernel_name()` raises `ValueError` (listing available kernels) when
an explicitly requested kernel can't be resolved — it no longer silently
falls back to python3. The lenient fallback is only used for names read from
notebook metadata (auto-start path), via `fallback="python3"`.

## Module Structure

### Core Modules (Entry Points)

- `extension.py` - Jupyter Server extension (`MCPExtensionApp`)
- `mcp_server.py` - FastMCP server wrapper (`MCPServer`)
- `code_mode_images.py` - `CodeModeImageForwarder` middleware: images from
  tools called in Code Mode scripts reach the `execute` response
- `mcp_handler.py` - Authenticated `{base_url}mcp` Tornado handler (`MCPHandler`)
- `asgi_bridge.py` - Runs the FastMCP Starlette app inside Tornado
  (`run_asgi`, `EmbeddedASGIApp` lifespan owner)
- `execution_tools.py` - Public MCP interface (16 tool functions, decorated with `@collaborative_tool`)

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
- `ydoc_helpers.py` - YDoc utilities, including `ensure_notebook_room()` which
  finds the notebook's room by API path, recreates cleaned-up rooms (via
  `get_document(create=True)`) to preserve CRDT history, and returns a
  `NotebookRoom` the adapter keeps alive and follows if replaced, and initializes rooms the browser has not loaded yet

### Foundation/Utilities

- `notebook_models.py` - Dataclasses (CellIdentifier, ExecutionResult, etc.)
- `notebook_config.py` - Configuration constants (EXECUTION_CONFIG, OUTPUT_FILTER_CONFIG, IMAGE_CONFIG)
- `kernel_utils.py` - Kernel detection and language resolution
- `kernel_tracker.py` - Tracks kernel sessions per notebook
- `output_filter.py` - Filter large outputs for token efficiency
- `cell_images.py` - Extract viewable images from code outputs and markdown
  embeds (`CellImages`)
- `execution_progress.py` - MCP progress notifications during cell execution
  (`ExecutionProgress`)
- `output_converter.py` - Output format conversion
- `notebook_io.py` - File I/O operations
- `path_utils.py` - Path normalization utilities
- `server_context.py` - Server context management
- `execution_utils.py` - Low-level kernel execution primitives

## MCP Functions (Public API)

All functions in `execution_tools.py` (16 tools):

**Kernel/session management:**

1. **list_notebook_kernels()** - Get available kernel specs (the ONLY way to list kernels)
2. **list_notebook_sessions()** - List active kernel sessions (the ONLY way to list sessions)
3. **setup_notebook(path, kernel_name, force_new_kernel)** - Create notebook and start/reuse kernel
4. **switch_notebook_kernel(path, kernel_name)** - Switch to different kernel
5. **shutdown_notebook(path, shutdown_kernel)** - Shutdown kernel session
6. **interrupt_notebook_kernel(path)** - Stop the running cell (KeyboardInterrupt); variables kept
7. **restart_notebook_kernel(path)** - Restart the same kernel; all state cleared

**Cell operations (fine-grained, one tool per operation):**

8. **add_code_cell(path, source, position_index, execute)** - Add code cell (executes by default)
9. **edit_code_cell(path, position_index, source, execute)** - Edit code cell (re-executes by default)
10. **add_markdown_cell(path, source, position_index)** - Add markdown cell
11. **edit_markdown_cell(path, position_index, source)** - Edit markdown cell
12. **delete_cell(path, position_index)** - Delete cell (later cells shift down)
13. **execute_cell(path, position_index)** - Execute an existing code cell

**Read-only queries:**

14. **view_notebook(path)** - All cells in order (list index == position_index)
15. **read_cell(path, position_index/cell_id/execution_count)** - One cell;
    the returned dict includes `position`
16. **view_cell_image(path, position_index/cell_id/execution_count)** - The
    cell's plots, or a markdown cell's embedded images, as MCP image content
    (works inside Code Mode scripts)

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

339 tests total across:

- `tests/test_execution_progress.py` - Progress reporter (throttling, heartbeat,
  messages, failure handling)
- `tests/test_kernel_control.py` - Interrupt/restart tools and the lookup-only
  `find_kernel_for_notebook`
- `tests/test_notebook_execution_tools.py` - Main test suite (MCP tool functions,
  including live output streaming)
- `tests/test_ydoc_adapter.py` - YDocAdapter source/output operations against a
  real YNotebook (edit_cell, append/coalesce/clear)
- `tests/test_ydoc_helpers.py` - YDoc room recreation (`ensure_ynotebook`) tests
- `tests/integration/test_ydoc_room_identity.py` - With a symlinked
  `root_dir`: finding JupyterLab's room, keeping it alive, following a
  replaced room, refusing the file fallback while a room exists
- `tests/integration/test_ydoc_rooms.py` - `ensure_ynotebook` against a real
  jupyter_server_ydoc (headless room load and reuse, unopened rooms, the
  configured ystore). Its server config puts the ystore and file ID
  databases under `tmp_path`; by default they land in the process CWD and in
  the user's real Jupyter data dir
- `tests/test_kernel_reuse.py` - Kernel reuse and session matching against fakes
  that reproduce jupyter_server's 404-for-dead-kernel contract
- `tests/test_imports.py` - Import validation tests
- `tests/test_kernel_utils.py` - Kernel utilities tests
- `tests/test_mcp_server.py` - MCP server tests (including Code Mode sandbox
  limits)
- `tests/test_extension.py` - Extension tests (endpoint traits, both serving modes)
- `tests/test_cell_images.py` - Image extraction and the `view_cell_image` tool
- `tests/test_code_mode_images.py` - Image forwarding through Code Mode
  `execute` (real FastMCP client)
- `tests/test_asgi_bridge.py` - ASGI bridge (scope, streaming flushes, disconnects,
  lifespan task)
- `tests/integration/test_mcp_endpoint.py` - Starts a **real** Jupyter Server
  (pytest-jupyter) and talks to `{base_url}mcp` with a real FastMCP client: auth,
  base_url, custom path, disabled endpoint, 503. pytest-jupyter fixtures are
  imported in `tests/integration/conftest.py` only, so its async runner does not
  interfere with the pytest-asyncio unit tests. Note `jp_fetch` mutates the
  `headers` dict you pass (adds auth) — always pass a fresh dict.
- `tests/conftest.py` - Only enables pytest-asyncio; fixtures live with their
  tests
- `tests/integration/conftest.py` - Marks every test in the directory
  `integration` (CI runs `-m "not integration"` and `-m integration` as
  separate steps) and, after each test, closes the execution engine's cached
  kernel clients and clears the process-wide singletons

Apart from `tests/integration/`, tests use mocks (no actual kernels/servers
started).

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

### MCP Endpoint and Authentication

- By default the MCP app is served **in-process** by `MCPHandler` at
  `{base_url}<mcp_endpoint_path>` (default `mcp`) on the Jupyter port. Under
  JupyterHub that is `/user/<name>/mcp` through the Hub proxy.
- `MCPHandler.prepare()` requires a Jupyter-authenticated user (403 otherwise,
  never a login redirect) and `execute` permission on `kernels`. Jupyter's
  `check_host` and XSRF rules still apply: a server with auth disabled treats
  requests as anonymous and rejects MCP `POST`s for missing XSRF — by design.
- FastMCP still implements the whole transport (sessions, SSE, protocol
  negotiation, Code Mode). `asgi_bridge.run_asgi` only translates the Tornado
  request into an ASGI scope and flushes each response chunk.
- `EmbeddedASGIApp` keeps the Starlette lifespan (FastMCP session manager) in a
  single background task — anyio cancel scopes must exit in the task that
  entered them. FastMCP's server lifespan is ref-counted, so the embedded app and
  the optional standalone uvicorn app can run together.
- `mcp_standalone = True` restores the 0.3.x separate listener on `mcp_port`.
  It has **no authentication**; it is off by default since 0.4.0. Its app is
  built by `build_standalone_app()` with FastMCP's strict Host/Origin guard
  (DNS-rebinding protection); the embedded app (`build_http_app()`) has no
  guard because Jupyter's `check_host` runs first.
- If FastMCP fails after response headers were sent, `MCPHandler` closes the
  connection so clients see a truncated stream, not a clean end.

### YDoc vs Nbformat Selection

The adapter is selected automatically in `get_notebook_adapter()` via
`ensure_notebook_room()`. `ensure_ynotebook()` returns just the document and
keeps its room alive first, since its callers write to it (the kernelspec on
a kernel switch). Attaching is retried (`_ATTACH_ATTEMPTS`) when the room is
evicted between `get_document()` and our check; any lookup that ends without
a document — `None`, a non-notebook document or an exception — goes through
the same room check below:

- If a YDoc room exists for the notebook → `YDocAdapter`
- If the room was cleaned up (browser disconnected for >60s) → the room is
  **recreated from the same ystore** and `YDocAdapter` is still used
- `NbformatAdapter` only if YDoc infrastructure is unavailable, no browser
  has connected yet, or the lookup failed **and the notebook positively has
  no room**. If it has one — or that cannot be told, e.g. the file ID lookup
  itself failed — `ensure_notebook_room()` raises instead: the room's file
  watcher would load our file writes over the live document, discarding the
  browser's unsaved changes. `_has_room()` first checks the room ID we last
  attached to for that path (`_known_room_ids`), which needs no file ID
  lookup. This also makes read-only tools fail on such an error, rather than
  return possibly stale file contents.

**Why recreate the room instead of falling back to NbformatAdapter?**
When the user's browser disconnects, `jupyter_server_ydoc` destroys the room
after `document_cleanup_delay` (60s). If we then wrote via `NbformatAdapter`,
the .ipynb on disk would advance while the ystore (CRDT op history) did not.
On browser reconnect, the new room detects the ystore/disk mismatch and resets
CRDT state — the browser's stored state vector no longer matches, and cells
appear **duplicated** in JupyterLab. `ensure_ynotebook()` recreates the room
with the same `file_id` (same ystore path), preserving CRDT history so
reconnecting browsers sync cleanly. Recreation uses jupyter_server_ydoc's
official `YDocExtension.get_document(copy=False, create=True)`
(jupyter-collaboration >= 4.4): it builds the room from the same ystore,
initializes its content, and holds the extension's per-room lock — the same
lock the browser websocket handler takes — so concurrent callers never create
duplicate rooms. Until the first browser connects (websocket server not
started) `ensure_ynotebook()` returns None and the file adapter is used: no
client holds CRDT state yet, and rooms with no websocket are never cleaned
up.

**Existing rooms are initialized too.** `get_document()` initializes only
rooms it creates; for an existing room it returns `room._document` as is. The
browser handler registers a room in `prepare()` but loads it only in
`open()`, so in between that document is **empty** — and edits to it are
autosaved over the .ipynb (`DocumentRoom` autosaves whether or not it is
ready). Both `get_ynotebook()` and `ensure_ynotebook()` therefore look the
room up (`ywebsocket_server.rooms`) and, if it is not `ready`, call
`room.initialize()` under `YDocExtension._room_locks[room_id]` — the lock the
handler's `open()` takes, so the two never load concurrently (initialize()
is a no-op once ready). This is the one private attribute we still rely on.

**Rooms we create use the configured ystore.** The handler builds a room's
ystore with `partial(ystore_class, config=...)`, but `get_document()`
instantiates the bare `ystore_class` (jupyter-collaboration 4.4.0–5.0.4), so
`SQLiteYStore.db_path` / `squash_after_inactivity_of` were ignored for rooms
it created: they read a different database than browser rooms, and a
reconnecting browser merged two unrelated histories (duplicated cells).
`_configure_ystore_class()` swaps `YDocExtension.ystore_class` for a subclass
that injects the extension's config before the first `create=True` call.
`get_document()` is the only runtime reader of that trait (handlers got their
class at startup). `tests/integration/test_ydoc_rooms.py` configures
`SQLiteYStore.db_path` to catch this.

**Rooms are looked up by API path.** JupyterLab names a notebook by its path
relative to `root_dir`; `ArbitraryFileIdManager` recognizes an absolute path
only by a string prefix match on the *unresolved* `root_dir`. Our paths are
resolved (`normalize_path()`), so under a symlinked root (macOS `/tmp`,
`/var`, symlinked homes, container volumes) they got a different file ID — a
different room — and every tool fell back to the file while JupyterLab had
the notebook open. `get_document()` and the file ID lookup now get
`to_api_path(path)`, which compares resolved paths. Covered by
`tests/integration/test_ydoc_room_identity.py` (symlinked `root_dir`).
Not covered: a symlinked subdirectory or notebook *inside* the root
(`normalize_path()` resolves it to a target JupyterLab names differently).

**A call keeps its room alive and follows it if it was replaced.**
`ensure_notebook_room()` returns a `NotebookRoom` (document + room). A tool
call can outlive the room: the handler deletes it `document_cleanup_delay`
after the last browser left, a deleted room **cancels its pending save**, and
its document is detached (edits to it vanish without an error). So:

- `NotebookRoom.keep_alive()` cancels the room's pending cleanup
  (`room.cleaner`, a public property; the handler's `open()` does the same for
  a reconnecting browser). `YDocAdapter` calls it on every **write** (reads
  do not, so viewing a notebook never pins its room), and while a cell runs
  in an open notebook the engine calls `adapter.keep_alive()` every
  `EXECUTION_CONFIG.room_keepalive_interval` (15s; a silent cell makes no
  other adapter calls). A room kept alive stays until a browser connects and
  leaves again.
- Before every operation `YDocAdapter._live()` checks the room is still the
  one `ywebsocket_server.rooms` serves; if not (e.g. deleted during a long
  silent cell) it re-attaches to the recreated room and bumps
  `adapter.generation`. The engine's output streamer then writes the cell's
  full outputs at the end instead of only the execution count, since the
  old room's last outputs may never have reached the disk the new room
  loaded.

### Persistence Strategy

When using `YDocAdapter` (notebook is open):
- Changes are written to YDoc only; JupyterLab autosave handles disk persistence
- Direct backend disk writes while the notebook is open cause JupyterLab to reload from the stale file, so they are intentionally avoided

When using `NbformatAdapter` (notebook is closed):
- Changes are written directly to the .ipynb file on disk

### Kernel Tracking and Reuse

Each notebook maintains its own kernel session via `kernel_tracker.py`. Kernels are reused across operations on the same notebook until explicitly shut down.

Kernel resolution (`KernelManager.get_kernel_for_notebook()`) reuses before
creating, in this order:

1. Locally tracked kernel (verified still alive)
2. Kernel from an existing Jupyter session (e.g., notebook open in JupyterLab)
3. Start a new kernel **with a Jupyter session** so it stays discoverable

Execution tools (`execute_cell`, `add_code_cell`, `edit_code_cell`) resolve
through this path automatically — they never fork a bare, sessionless kernel
when the tracker has no entry (that used to silently lose user state on
"reconnect"). If no session-backed kernel can be started (the
contents manager cannot open the path — outside `root_dir`, hidden — or
`create_session` fails), `_start_kernel_for_notebook()` raises
`KernelStartError` with the reason; nothing falls back to a bare kernel.
`switch_kernel()` resolves the kernel name before it stops the old kernel. Symmetrically, `setup_notebook` falls back to the tracked kernel
when no session exists, so reconnecting to a notebook always reattaches to the
running kernel unless `force_new_kernel=True`.

Session-to-notebook matching (`_find_session_for_notebook`) compares exact,
normalized, and `samefile` paths — deliberately **no filename-only matching**:
two notebooks with the same name in different folders must never share a
kernel. Non-notebook sessions (e.g., consoles) are skipped per-session, not
allowed to abort the scan.

### Live Output Streaming (open notebooks)

For collaborative (YDoc) notebooks, outputs are **streamed into the cell as
the kernel emits them**, so JupyterLab renders progress during long-running
executions instead of everything appearing when the MCP tool call returns:

- At execution start the cell's stale outputs are cleared.
- Each iopub output fires an `on_output` callback
  (`execution_utils.collect_outputs`) that appends it to the cell via
  `adapter.append_cell_output()`. The cell is re-located by `cell_id` before
  every append, so streamed output follows the cell if positions shift.
- Consecutive `stream` outputs with the same name are coalesced by appending
  to the previous entry's in-place pycrdt `Text` — chatty loops stay one
  outputs entry with small CRDT deltas, not thousands of Array inserts.
- When streaming delivered everything, the final write records only the
  `execution_count` (no clear+rewrite flash). If streaming failed or the
  notebook is closed (`NbformatAdapter` — nobody is watching the file), the
  full batch write reconciles the cell exactly as before.
- Streaming failures are logged and never abort execution; the MCP response
  (full `ExecutionResult`) is unaffected either way.

### Execution Internals

- **No stdin**: `execute_code_on_kernel()` sends `allow_stdin=False`, so
  `input()` / `getpass()` raise `StdinNotImplementedError` at once instead of
  blocking on an input request nobody answers until the timeout.
- **Stopping runaway cells**: `execute_code_on_kernel()` takes an `interrupt`
  callback (the engine passes `kernel_manager.interrupt_kernel`).
  - **On timeout**, it interrupts the kernel and drains output until idle,
    bounded by `EXECUTION_CONFIG.interrupt_grace_secs` (10s), so the next
    execution doesn't queue behind the runaway cell. The result is
    `status="error"`: "…; the kernel was interrupted", or "…still busy; use
    restart_notebook_kernel" if it ignores SIGINT.
  - **On cancellation** of the MCP request (fastmcp 4 propagates it into tools
    called from Code Mode), it re-raises immediately. A background cleanup task
    interrupts the kernel, drains to idle within the grace, and finishes both
    inner tasks. `ExecutionEngine` hands the per-kernel lock to that task
    (`_release_after`), so the next execution reaches the kernel only after
    the interrupted cell has ended. Don't wait inline here: anyio re-delivers
    cancellation at every await, so an inline drain gets abandoned and leaves a
    second iopub reader behind. Also, ipykernel aborts requests queued behind a
    failing cell.
  - **Either way**, both inner tasks (shell wait and iopub collector) are
    always finished, so no orphaned collector keeps reading the shared client.
  - An `aborted` execute_reply (the kernel skipped the cell because an earlier
    one failed or was interrupted) is reported as `status="error"` with a
    "re-run the cell" message instead of an empty success.
  - `interrupt_notebook_kernel` and `restart_notebook_kernel` resolve the
    kernel with `KernelManager.find_kernel_for_notebook()` (tracked kernel,
    then Jupyter session; never starts one). Restart keeps the kernel id, and
    the tool drops the engine's cached client (`forget_kernel_client`) so the
    next execution reconnects.

- **Progress notifications**: while `execute_cell` / `add_code_cell` /
  `edit_code_cell` run, `ExecutionEngine.execute_cell_code()` sends MCP
  `notifications/progress` for the current request via `ExecutionProgress`
  (`execution_progress.py`): on outputs (throttled to
  `PROGRESS_CONFIG.min_interval`) and as a heartbeat every
  `heartbeat_interval` while nothing else was sent. Message format:
  `"<notebook> cell <n> · <elapsed>s · <k> outputs · <last line>"` (or
  `· waiting for kernel` while another execution holds the kernel lock);
  `progress` = elapsed seconds, `total` unknown. It uses FastMCP's request
  context (`get_context()`), so it also works when the tool is called inside a
  Code Mode `execute` script, and needs no tool parameter. Outside an MCP
  request there is no reporter; a failing notification disables progress for
  that run but never affects execution. Clients only receive progress if they
  sent a progress token.

- The Code Mode sandbox (Monty) time budget is set explicitly by
  `build_sandbox_limits()` in `mcp_server.py`: `max_feed_duration_secs =
  SANDBOX_EXECUTION_BUDGET_SECS` (60s). Monty's clock pauses while a script
  waits on tool calls, so long cell executions do not count against it.
  Requires fastmcp >= 4.1 (pydantic-monty 1.x). Older Monty versions used a
  wall-clock `max_duration_secs` that kept ticking during tool calls, and
  they reject the new key. It also sets `max_memory =
  SANDBOX_MAX_MEMORY_BYTES` (100 MB, fastmcp's default): passing limits
  replaces fastmcp's defaults wholesale, and Monty treats an omitted key as
  unlimited.
- Kernel clients are cached per kernel in `ExecutionEngine`, and executions
  on the same kernel are serialized with a per-kernel asyncio lock (a shared
  client's iopub channel must have a single consumer). The cached client is
  dropped when the kernel disappears or a connection-level error occurs.
- The shell-reply wait and iopub collection run **concurrently** (required
  for live output streaming — the execute_reply only arrives when execution
  finishes) and share one deadline, so a hung kernel blocks at most
  `EXECUTION_CONFIG.default_timeout` (+ small grace), not 2x. The default
  is 36000s (10 hours), deliberately long for heavy cells. Channel
  timeouts raise `queue.Empty` in jupyter_client — both `Empty` and
  `TimeoutError` are handled. Stale shell replies are drained.
- After execution, the target cell is re-located by `cell_id` before outputs
  are written, so outputs land in the right cell even if the user
  added/deleted cells in JupyterLab while the code ran. If the cell was
  deleted, the output update is skipped (the result is still returned).
- All nbformat disk I/O and kernelspec scans run via `asyncio.to_thread` —
  never block the Jupyter server's event loop. Read-only operations
  (`view_notebook`, `read_cell`, finders) never write the file back.
- Editing a code cell resets its outputs/execution_count (both adapters), so
  stale outputs from the old source never linger.
- `switch_notebook_kernel` updates kernelspec metadata through the live YDoc
  when the notebook is open (disk write only for closed notebooks).

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
