# Changelog



## 0.6.1 (2026-10-17)

### Enhancements made

- **Cells run through MCP show `[*]` in JupyterLab while they execute.** The
  cell's transient `execution_state` is set to `"running"` at execution
  start and back to `"idle"` after the final write (in the background if the
  call fails or is cancelled). Open notebooks only (JupyterLab >= 4.1);
  jupyter_ydoc never saves the field.

### Documentation

- README: shorter setup section; fixed the link to the full example config.

## 0.6.0 (2026-10-16)

### Breaking changes

- **Renamed to `jupyter-mcp-live`** (import name `jupyter_mcp_live`). Update:
  - `mcp_tools` entries: `"jupyter_mcp_executor.execution_tools:..."` →
    `"jupyter_mcp_live.execution_tools:..."`;
  - the server extension: `jpserver_extensions` key `jupyter_mcp_live`
    (enabled automatically by the installed
    `jupyter_server_config.d/jupyter_mcp_live.json`);
  - the extension's own config file: `jupyter_jupyter_mcp_executor_config.py`
    → `jupyter_jupyter_mcp_live_config.py` (`c.MCPExtensionApp` settings in
    `jupyter_server_config.py` or a `--config` file are unaffected);
  - third-party tool entry points: group `jupyter_mcp_live.tools`;
  - the logger: `jupyter_mcp_live`.

  Uninstall `jupyter_mcp_executor` before installing: with both installed,
  two extensions would serve the same MCP endpoint.

### Enhancements made

- **New tools `interrupt_notebook_kernel` and `restart_notebook_kernel`.**
  Interrupt stops the running cell with KeyboardInterrupt and keeps the
  variables. Restart keeps the same kernel (and JupyterLab session) but clears
  all state. Both work on cells started from JupyterLab too, and neither ever
  starts a kernel.
- **Runaway cells are interrupted automatically:**
  - when they hit the execution timeout, the kernel is interrupted and drained
    to idle (bounded by `interrupt_grace_secs`, 10s), so the next execution
    no longer queues behind them;
  - when the MCP request is cancelled.
- **New tool `view_cell_image`** shows the model a cell's plots as real
  images (PNG/JPEG/GIF/WEBP, at most 4 per call) instead of the
  `[Filtered image/png ...]` placeholder. It also works from Code Mode
  scripts: the `CodeModeImageForwarder` middleware moves image blocks out of
  nested `call_tool()` results (the script gets the JSON summary, never
  base64) and attaches them to the `execute` response (at most 8).
- **`view_cell_image` also shows images embedded in markdown cells**: pasted
  or dragged images (`attachment:` references) and `data:` URIs, from both
  `![](...)` and `<img src>`. Remote URLs and local image files are listed
  in `skipped` with their target. Summary entries now carry a `source` field
  (`output`, `attachment`, `data_uri`, `url`, `file`) and the summary a
  `cell_type`.

### Changed

- `read_cell` and `view_notebook` return each cell's id as `cell_id` (the
  name `read_cell` and `view_cell_image` take it under), besides `id`. The
  descriptions of `view_notebook` and `list_notebook_sessions` say their
  list arrives as `{"result": [...]}`, as FastMCP wraps list results.
- Tools are registered with FastMCP as they are. A wrapper rewrote `dict`
  parameters to also accept a JSON string (`anyOf [object, string]`),
  which made schemas ambiguous for Code Mode codegen; tools are only
  reached through Code Mode scripts, whose `call_tool()` passes objects as
  JSON values. A JSON string for a `dict` parameter is now a validation
  error. `register_tools({"name": func})` now registers the tool under that
  name. The 16 notebook tools' schemas are unchanged. (`json:dumps` is gone
  from the README and example config: FastMCP rejects `**kwargs`, so it
  never registered.)
- **`jupyter-ai-tools`, `jupyterlab_commands_toolkit` and
  `jupyterlab_notebook_awareness` are no longer installed.** They provided
  `@collaborative_tool`, whose per-notebook "AI is editing" indicator never
  ran (it looks for a `file_path` parameter; the tools take
  `notebook_path`); it only set a global "Jupyter AI User" awareness entry.
  Tools from those packages can still be listed in `mcp_tools`; install the
  packages yourself.
- **The MCP endpoint also requires `read` and `write` permission on
  `contents`**, besides `execute` on `kernels`: the tools read and modify
  notebooks, and a user the authorizer keeps from modifying files (e.g. a
  read-only collaborator) could do it through MCP. Nothing changes with
  Jupyter's default authorizer or JupyterHub's `access:servers`, which grant
  every permission; custom authorizers get `403` for such users.
- **`add_code_cell` / `add_markdown_cell` reject a `position_index` outside
  0..cell count** with an error, instead of silently appending at the end
  (the agent then reported a position the cell was not at). Omit it to
  append.
- **`list_notebook_kernels` returns only what an agent needs**:
  `{"default_kernel": ..., "kernelspecs": {name: {"display_name",
  "language"}}}`. Each kernelspec's `argv`, `env` and `resource_dir` cost
  tokens and `env` may hold secrets.
- Placeholders for filtered images say to call `view_cell_image`.
- **pandas, numpy, seaborn, matplotlib and scipy are no longer installed
  with the extension.** The extension never imported them; they belong in
  the kernel's environment. Install them there if your notebooks use them
  (`pip install pandas matplotlib …` in the kernel's environment).
- **Notebooks must be inside the Jupyter root directory.** Absolute paths
  outside it (or reaching outside through a symlink) are rejected with an
  error. They never worked fully: Jupyter cannot open them, so they had no
  kernel session and no live document, and edits went to a file JupyterLab
  did not show.
- Dependency floors match what the code uses: `nbformat>=5.1` (cell ids),
  `jupyter_ydoc>=3.4`, and `tornado>=6.5.9` is declared (imported directly;
  fixes pre-authentication request-parsing DoS advisories). CI now fails if
  `pip-audit` reports a known vulnerability in the installed dependencies.
- The Code Mode sandbox keeps a 100 MB memory cap. Passing our own limits
  had replaced fastmcp's defaults, and Monty treats an omitted key as "no
  limit".
- The example config no longer listens on `0.0.0.0` or hard-codes a token
  (the published `BLOCK`); README and INSTALL take the token from
  `JUPYTER_TOKEN` too.
- **Requires fastmcp >= 4.1** (`fastmcp[code-mode]>=4.1,<5`). Support for
  fastmcp 3.x and 4.0.x is dropped; they still ship pydantic-monty 0.0.x. The
  old sandbox-limit code path is removed: there is no `max_duration_secs` and
  no `SANDBOX_TIMEOUT_HEADROOM_SECS`. CI's oldest-versions job now tests
  fastmcp 4.1.0 with Jupyter Server 2.16.0 on Python 3.11.
- **Requires jupyter-collaboration >= 4.4** (jupyter-server-ydoc >= 2.4).
  `ensure_ynotebook()` now recreates a cleaned-up YDoc room with the official
  `YDocExtension.get_document(copy=False, create=True)` instead of building a
  `DocumentRoom` and ystore by hand from private APIs. Behavior is unchanged
  (same ystore, content loaded, file adapter until the first browser
  connects), and room creation now shares jupyter_server_ydoc's per-room
  lock with the browser websocket handler instead of a private one.
  `get_document()` ignores the ystore configuration for the rooms it creates
  (e.g. `c.SQLiteYStore.db_path`), so those rooms would have read another
  CRDT history than browser rooms, and a reconnecting browser duplicated
  cells; the extension's ystore class is now given the server config first.

### Bugs fixed

- **Filtered plots keep their output type in tool responses.** The placeholder
  for a large image or HTML output always said `execute_result` (with
  `execution_count: null`), even for `display_data` such as `plt.show()`. It
  now keeps the original type; the notebook itself was never affected.
- **The startup log no longer names an unused port.** "Initialized MCP
  server ... on localhost:3001" was logged even with the standalone listener
  off; the endpoint actually served is logged separately.
- **Cells added or edited through the tools no longer come back duplicated
  after the browser reconnects.** The room's first ops before it observed its
  document (our first edit after creating the room, and on 4.4 the
  `dirty = False` of its initialization) never reached the ystore, so all
  later edits of that client stayed pending in it. After the browser was
  away longer than `document_cleanup_delay`, the recreated room lacked them,
  reloaded the file as new cells, and the browser added the originals back.
  The room's full state is now written to its ystore when we first attach
  to it.
- Editing a cell of an open notebook no longer replaces its whole metadata
  map: only changed keys are set and removed ones deleted, in one
  transaction. Replacing the map broadcast all of it to every browser and
  lost metadata changes other collaborators made at the same time (e.g. a
  tag added in JupyterLab), and a source-only edit re-sent the metadata.
- Notebooks that are not open in JupyterLab are now saved through Jupyter's
  contents manager, as JupyterLab saves them, instead of being written with
  nbformat directly:
  - HTML/JavaScript outputs of cells run by the tools are trusted, so
    JupyterLab renders them instead of sanitizing them. Outputs that were
    not trusted before stay untrusted.
  - The notebook's first checkpoint is created, save hooks run (e.g.
    jupytext pairing), and the write is atomic.
  - Contents manager rules apply: hidden notebooks are refused unless
    `allow_hidden`, and non-filesystem contents managers work.
  - Concurrent edits of one notebook (e.g. a Code Mode script gathering
    several `add_code_cell` calls) no longer overwrite each other.
- Without a kernel name, `setup_notebook` (and executing in a notebook whose
  metadata names no kernel) now starts the server's default kernel
  (`MappingKernelManager.default_kernel_name`, as JupyterLab does) instead
  of always `python3`. `setup_notebook`'s `kernel_name` defaults to `None`.
  If the configured default kernel is not installed, both paths fail with
  an error listing the available kernels: the lenient fallback for kernel
  names from notebook metadata is now used only if it is installed (the
  old `python3` fallback was not checked either, and failed later at
  kernel start).
- `read_cell` / `view_notebook` on an open notebook reported numbers as
  JupyterLab's Yjs stores them: a cell run there had `execution_count: 5.0`.
  Cells are now read with `YNotebook.get_cell()`, as jupyter_ydoc saves them
  (integral floats in outputs and metadata, e.g. a JSON output's `1.0`, read
  as `1`, as they are written to disk).
- Adding or editing a cell in an open notebook no longer writes an
  `activeCellIndex` key to the shared notebook metadata. Nothing in
  JupyterLab reads it (the active cell is a widget property), so every
  edit only cost an extra broadcast and autosave.
- YDoc rooms the tools used without a browser are now cleaned up. A room
  created while the notebook was not open in JupyterLab, or one edited after
  the last browser left, stayed in memory with a file watcher polling every
  second until the server stopped. Such rooms are now deleted
  `document_cleanup_delay` after the tools last wrote to them (and after
  their autosave, including a save still in progress), as
  jupyter_server_ydoc does once the last browser leaves.
  A running cell keeps its room, and a browser opening the notebook cancels
  the cleanup. With autosave off (`document_save_delay = None`) rooms are
  kept, since deleting them would discard the edits.
- Kernel output messages are handled as JupyterLab handles them, in the
  notebook and in the MCP response:
  - `clear_output` was ignored, so every frame of a progress loop
    (`clear_output(wait=True)` + redraw: tqdm, live plots) piled up; now the
    cell keeps the last frame. `wait=True` clears when the next output
    arrives.
  - `update_display_data` was dropped, so a display updated through its
    handle kept its first content; now it shows the update.
  - Each print was its own output; consecutive stream chunks are now one.
  - In the MCP response (not the notebook), lines redrawn with `\r` (tqdm
    on stderr) show only their last frame.
  - Progress notifications count redrawn frames too.
- An MCP response carries at most 30 outputs per cell (the first and last
  15, with a marker for the rest); a cell calling `display()` in a loop
  returned thousands. The notebook keeps all of them.
- If another server-side client connected to a notebook's kernel after
  ours (e.g. an extension calling `km.client()`), cells sent by the MCP
  tools never ran: they timed out and the error advised restarting the
  kernel. Both clients cloned the kernel manager's session, so they shared
  one socket identity, and ipykernel hands an identity over to its newest
  connection. The tools' kernel client now has its own session.
- With the Jupyter root directory behind a symlink (macOS `/tmp` and
  `/var`, symlinked home or project directories, container volumes), the
  tools never found the notebook's YDoc room: they looked it up by the
  resolved absolute path, which the file ID service maps to another file ID
  than JupyterLab's. Every edit then went to the `.ipynb` while JupyterLab
  had the notebook open, and the room's file watcher loaded it over the live
  document (unsaved browser edits lost; cells duplicated after reconnect).
  Rooms are now looked up by API path, as JupyterLab does, and sessions get
  a relative path under such a root too.
- A tool call could outlive its YDoc room: 60 s after the browser left,
  jupyter_server_ydoc deletes the room, cancelling its pending save, and
  writes to its detached document vanished without an error (outputs of a
  long cell missing after reopening). Editing a room, or running a cell in
  it, now cancels its pending cleanup, and an adapter whose room was
  replaced anyway follows the new room and rewrites the cell's full outputs
  at the end.
- If the YDoc lookup failed while the notebook had a room, the tools fell
  back to writing the `.ipynb` behind it, with the same overwrite. They now
  fail with an error instead, also when it cannot be told whether a room
  exists; the file is used only when the notebook has no room.
- `shutdown_notebook(shutdown_kernel=False)` reported "kernel still running"
  but shut the kernel down: Jupyter cannot delete a session without stopping
  its kernel. The session and kernel are now left running. With the default
  (`True`), the reply names the kernel that was shut down also when only
  JupyterLab's session knew it (it said "no kernel was tracked"), and
  `shutdown_notebook`, `switch_notebook_kernel` and
  `setup_notebook(force_new_kernel=True)` no longer shut the same kernel down
  twice (a "Kernel does not exist" warning per call).
- When no kernel could be started for a notebook (e.g. a path outside the
  Jupyter root directory, which Jupyter's contents manager cannot open),
  `setup_notebook` and `switch_notebook_kernel` failed with a pydantic
  `ValidationError` ("kernel_id: Input should be a valid string"), and the
  execution tools started a bare kernel with no Jupyter session that
  JupyterLab could not see. All of them now fail with `KernelStartError`
  and the reason. `switch_notebook_kernel` also checks the new kernel name
  before it shuts the old kernel down.
- A cell that called `input()` blocked until the execution timeout (10 hours
  by default): it was executed with stdin allowed, and nobody answers the
  kernel's input request. Executions now refuse stdin, so `input()` raises
  `StdinNotImplementedError` immediately.
- An MCP call that arrived after a browser had registered a notebook's YDoc
  room but before the room was loaded got the room's still-empty document:
  reads saw no cells, and edits were autosaved over the notebook on disk.
  The room is now initialized first, under the same per-room lock the
  browser's websocket handler takes.
- A cancelled execution left its iopub collector running until the deadline,
  up to 10 hours, reading the shared kernel client's channel. Both inner tasks
  are now always finished, and the kernel stays reserved until the cancelled
  cell has ended.
- A cell that ipykernel aborted (queued behind a failing or interrupted cell)
  was reported as a successful run with no output. It is now an error that
  says to re-run the cell.
- Timeout interrupts no longer depend on timer precision. jupyter_client can
  report "no reply" a few ms before the requested timeout; the timeout then
  went undetected, the kernel was not interrupted, and the call returned
  "timed out while collecting outputs" 5s later. Seen on Linux CI.
- Once the kernel reports idle, a missing execute_reply is waited for at most
  2s instead of until the execution deadline, which can be hours away.
- No more ERROR traceback ("Failed to recreate YDoc room … WebsocketServer is
  not running") on every notebook operation before any browser has connected.
  Falling back to the file is correct then, and is now logged at debug level.
- Under JupyterHub, the startup log shows the public MCP endpoint
  (`JUPYTERHUB_PUBLIC_URL`, else the `/user/<name>/` path) instead of the
  single-user server's internal address.
- `pytest.ini` used the setup.cfg header `[tool:pytest]` and was ignored by
  pytest; it now takes effect (see the file for the kept settings).

### Maintenance

- Requires jupyter-collaboration 5.x (was 4.4): jupyter-server-ydoc 3.x,
  jupyter_ydoc >= 4.1.1 and jupyter_server >= 2.19 (what jupyter-server-ydoc
  3.0 requires). CI's oldest-versions job tests jupyter-collaboration 5.0.0.
- CI: actions pinned by commit SHA (Dependabot proposes updates), Python
  3.12 and 3.13 added to the matrix (3.11 oldest, 3.x newest), coverage
  reported once per run (unit + integration; `pytest-cov` is now a test
  extra), and warnings are errors: any warning from this package, and
  resource leaks outside the real-server tests. Test fixtures now close the
  signature stores they open.
- Official APIs instead of hand-written equivalents: jupyter_server's
  `@authorized` for the MCP endpoint's permission check (GET, POST and
  DELETE all need `execute` on `kernels`), `kernel_id in kernel_manager` for
  kernel liveness, `YNotebook.get_cell()` for reading cells. Kernel output
  messages are still converted by hand: `nbformat.v4.output_from_msg`
  raises on fields some kernels omit and validates every output against
  the JSON schema, too slow for chatty cells.
- `KernelManager.setup_notebook` split into a short orchestrator and
  focused helpers (reuse, force-new replacement, notebook creation), with
  characterization tests for its kernel reuse and ordering; no behavior
  change.

## 0.5.0 (Unreleased)

### Enhancements made

- **MCP progress notifications during cell execution.** `execute_cell`,
  `add_code_cell` and `edit_code_cell` send `notifications/progress` while the
  cell runs, e.g. `analysis.ipynb cell 3 · 12s · 5 outputs · tick 4`. They are
  throttled to one per second on output, with a heartbeat every 30s for silent
  cells and `waiting for kernel` while another execution holds the kernel.
  This also works through Code Mode `execute`, and tool signatures are
  unchanged. Clients that reset request timeouts on progress no longer treat
  long, quiet cells as hung.

### Changed

- **Default execution timeout raised from 1 hour to 10 hours**
  (`EXECUTION_CONFIG.default_timeout = 36000`), for heavy, long-running cells.
  A hung kernel can now block a tool call for up to 10 hours before it times
  out. On fastmcp 3.x the Code Mode sandbox budget grows with it (timeout +
  300s), while fastmcp 4.x budgets don't count kernel time at all. Client
  tool-call limits also apply; the README's "Long-running cells" section covers
  Claude Code's idle timeout (which the 30s progress heartbeat keeps alive) and
  its wall-clock timeout.

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
