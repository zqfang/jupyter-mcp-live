# Changelog



## 0.5.0 (Unreleased)

### Enhancements made

- **MCP progress notifications during cell execution.** `execute_cell`,
  `add_code_cell` and `edit_code_cell` send `notifications/progress` while the
  cell runs, e.g. `analysis.ipynb cell 3 · 12s · 5 outputs · tick 4`. They are
  throttled to one per second on output, with a heartbeat every 5s for silent
  cells and `waiting for kernel` while another execution holds the kernel.
  This also works through Code Mode `execute`, and tool signatures are
  unchanged. Clients that reset request timeouts on progress no longer treat
  long, quiet cells as hung.

### Bugs fixed

- The contents-manager preflight before creating a kernel session was never
  awaited with Jupyter Server's async contents manager, so it never checked
  anything and emitted a `RuntimeWarning`. It is now awaited, and uses
  `content=False` so it no longer reads the whole notebook.

## 0.4.1 (Unreleased)

### Enhancements made

- **fastmcp 4 support** (`fastmcp[code-mode]>=3.4.3,<5`). fastmcp 4 ships
  pydantic-monty 1.x, which renamed the Code Mode sandbox time limits and
  rejects the old `max_duration_secs`, so every Code Mode `execute` failed.
  `build_sandbox_limits()` now picks the limit the installed Monty supports.
  On Monty 1.x the clock pauses while a script waits on tool calls, so the
  budget (60s) covers only the script itself, and long cell executions no
  longer need the extra headroom.
- CI also tests fastmcp 3.x on Python 3.11.

## 0.4.0 (Unreleased)

### Breaking changes

- **MCP is now served by Jupyter Server itself, behind Jupyter authentication.**
  The endpoint is `{base_url}mcp` on the Jupyter port (e.g.
  `http://localhost:8888/mcp`) and requires the Jupyter token
  (`Authorization: Bearer <token>` or `token <token>`).
- **The separate listener on `mcp_port` is off by default.** It has no
  authentication. Set `c.MCPExtensionApp.mcp_standalone = True` to bring it
  back on a single-user machine. It now rejects foreign `Host` (421) and
  `Origin` (403) headers, blocking DNS-rebinding attacks from web pages.

- **Dependency floors raised:** `jupyter_server>=2.16` (the first release that
  runs extensions' async start hook; on older 2.x the MCP server never started)
  and `fastmcp[code-mode]>=3.4.3,<4` (Host/Origin guard API; fastmcp 4 ships
  Monty 1.x, whose sandbox limits are incompatible with `build_code_mode()`).

### Enhancements made

- **JupyterHub support**: each single-user server exposes
  `https://<hub>/user/<name>/mcp` through the Hub proxy, authenticated with a
  Hub API token. No extra ports, no port collisions between users.
- New traits: `mcp_jupyter_endpoint` (default True), `mcp_endpoint_path`
  (default `"mcp"`), `mcp_standalone` (default False).
- FastMCP still handles the full streamable-HTTP transport (sessions, SSE,
  protocol negotiation, Code Mode); a small in-process ASGI bridge
  (`asgi_bridge.py`) runs it inside Tornado, and `mcp_handler.py` enforces
  Jupyter auth (`execute` permission on `kernels`).
- First integration tests against a real Jupyter Server
  (`tests/integration/`).

## 0.2.0 (Unreleased)

### Enhancements made

- **Custom Kernel Support** - Added intelligent kernel resolution system for AI agents
  - **New module:** `jupyter_mcp_executor/kernel_utils.py` (230 lines)
  - **Features:**
    - Smart kernel name resolution with 3-tier strategy (exact match → language match → pattern detection)
    - Runtime kernel spec querying (no hardcoded mappings)
    - Support for custom kernel names (e.g., "rapids", "r45", "julia-1.10")
    - Language-based kernel discovery (request "R" and get best available R kernel)
    - Pattern-based language detection fallback
  - **Functions added:**
    - `detect_kernel_language_from_spec()` - Detect language from kernel specifications
    - `detect_kernel_language_from_name()` - Pattern-based language detection
    - `find_kernel_by_language()` - Find kernels matching a language
    - `resolve_kernel_name()` - Smart kernel name resolution
    - `get_kernel_info()` - Get kernel language and display name
  - **Files changed:**
    - `jupyter_mcp_executor/kernel_manager.py` - Integrated smart kernel resolution
    - `jupyter_mcp_executor/notebook_config.py` - Replaced `KERNEL_ALIASES` with `LANGUAGE_PATTERNS`
  - **Tests added:** 29 comprehensive tests in `tests/test_kernel_utils.py`
  - **Impact:** AI agents can now create notebooks with any custom kernel name without hardcoded aliases
  - **Backwards compatible:** All existing kernel names continue to work

### Code Quality Improvements

- **Ruff Linting Cleanup** - 100% reduction in linting errors
  - **Before:** 140 ruff errors across production and test code
  - **After:** 0 errors (all checks passed)
  - **Changes:**
    - Migrated to modern `pathlib` API (replaced `os.path.*` methods)
    - Added proper `ClassVar` type annotations for class-level constants
    - Fixed bare except clauses
    - Organized imports (auto-fix + manual cleanup)
    - Added `# noqa: PLC0415` comments for intentional local imports (circular dependency avoidance)
  - **Files affected:**
    - `jupyter_mcp_executor/kernel_manager.py` - Pathlib migration
    - `jupyter_mcp_executor/path_utils.py` - Complete pathlib rewrite
    - `jupyter_mcp_executor/output_filter.py` - ClassVar type annotations
    - `jupyter_mcp_executor/notebook_adapter.py` - Documented intentional imports
    - `jupyter_mcp_executor/query_service.py` - Documented intentional imports
    - `jupyter_mcp_executor/ydoc_helpers.py` - Documented intentional imports
    - All test files - Import organization and modern patterns
  - **Test status:** All 135 tests passing

### Bug Fixes

- **[CRITICAL]** Fixed code execution failure with "Invalid message type: coroutine" error
  - **Root cause:** Incorrectly using `run_in_executor()` with async `AsyncKernelClient.get_iopub_msg()` method
  - **Solution:** Directly await the async method instead of wrapping it in a thread pool executor
  - **Impact:** Restores all code execution functionality for both Python and R kernels
  - **Files changed:** `jupyter_mcp_executor/notebook_execution_tools.py` (line 309-313)
  - **Affects:** `execute_cell()`, `modify_notebook_cells()` with `execute=True`, `execute_notebook_code()`

- **[CRITICAL]** Fixed missing `execution_count` in notebook cells after code execution
  - **Root cause:** Only capturing `execution_count` from `execute_result` messages on IOPub channel, which are not sent for code that only produces print output
  - **Solution:** Read `execution_count` from `execute_reply` message on Shell channel, which is ALWAYS present according to Jupyter messaging protocol
  - **Impact:** Execution counts now properly populate in notebook files for all types of code execution
  - **Files changed:**
    - `jupyter_mcp_executor/notebook_execution_tools.py` (lines 293-325, 368-383)
    - `tests/test_notebook_execution_tools.py` (lines 76, 83-145, 343-397)
  - **Affects:** All code execution functions: `execute_cell()`, `modify_notebook_cells()`, `execute_notebook()`, `execute_notebook_code()`
  - **User benefit:** Cells can now be referenced by execution count `[1]`, `[2]`, etc. in JupyterLab UI

### Documentation improvements

- Updated `CLAUDE.md` with comprehensive kernel utilities documentation
- Added custom kernel support examples and usage patterns
- Documented 3-tier kernel resolution strategy


## 0.1.2

([Full Changelog](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/compare/v0.1.1...754ead215d3a9bb6e46f9e8d874bb9911caec3eb))

### Enhancements made

- Add automatic tool discovery via Python entrypoints [#10](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/10) ([@Zsailer](https://github.com/Zsailer))

### Documentation improvements

- Add screencast to the README [#9](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/9) ([@jtpio](https://github.com/jtpio))
- Add Gemini CLI configuration [#6](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/6) ([@jtpio](https://github.com/jtpio))
- Update README to the new toolkit [#5](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/5) ([@jtpio](https://github.com/jtpio))

### Contributors to this release

([GitHub contributors page for this release](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/graphs/contributors?from=2025-09-11&to=2025-10-24&type=c))

[@jtpio](https://github.com/search?q=repo%3Ajupyter-ai-contrib%2Fjupyter-mcp-executor+involves%3Ajtpio+updated%3A2025-09-11..2025-10-24&type=Issues) | [@Zsailer](https://github.com/search?q=repo%3Ajupyter-ai-contrib%2Fjupyter-mcp-executor+involves%3AZsailer+updated%3A2025-09-11..2025-10-24&type=Issues)


## 0.1.1

([Full Changelog](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/compare/fb0b9a59e08150ce6c285a9af41dac2342e7d181...6bf57b34817b2b631f91afb8bab6a72d48fa7a5b))

### Maintenance and upkeep improvements

- Add releaser workflows [#3](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/3) ([@jtpio](https://github.com/jtpio))

### Other merged PRs

- Add automatic JSON argument conversion for MCP tools [#4](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/4) ([@Zsailer](https://github.com/Zsailer))
- Rename to jupyter-mcp-executor [#2](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/2) ([@Zsailer](https://github.com/Zsailer))
- Initial implementation of an MCP server as an extension [#1](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/pull/1) ([@Zsailer](https://github.com/Zsailer))

### Contributors to this release

([GitHub contributors page for this release](https://github.com/jupyter-ai-contrib/jupyter-mcp-executor/graphs/contributors?from=2025-08-14&to=2025-09-11&type=c))

[@jtpio](https://github.com/search?q=repo%3Ajupyter-ai-contrib%2Fjupyter-mcp-executor+involves%3Ajtpio+updated%3A2025-08-14..2025-09-11&type=Issues) | [@Zsailer](https://github.com/search?q=repo%3Ajupyter-ai-contrib%2Fjupyter-mcp-executor+involves%3AZsailer+updated%3A2025-08-14..2025-09-11&type=Issues)
