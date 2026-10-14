# Using Jupyter MCP Executor on JupyterHub

Under JupyterHub, the MCP endpoint runs inside each user's own single-user
server. The Hub proxy routes to it and the Hub's token authenticates it, so you
don't open extra ports or configure the proxy.

```
MCP client ──Bearer <hub-api-token>──▶ https://<hub-host>/user/<username>/mcp
                                         │  (Hub proxy)
                                         ▼
                                single-user jupyter-server + MCP extension
```

Each user's endpoint can only reach that user's own kernels and files.

## 1. Install into the single-user environment

Install the package where the user's `jupyter-server` runs, **not** where the
Hub runs. It is not published to PyPI yet, so install it from GitHub.

**Admin, custom user image** (for example Zero to JupyterHub):

```dockerfile
FROM quay.io/jupyter/scipy-notebook:latest
RUN pip install --no-cache-dir "git+https://github.com/zqfang/jupyter-mcp-executor.git"
COPY jupyter_server_config.py /etc/jupyter/jupyter_server_config.py
```

**Regular user** who can pip-install into the environment their server uses
(from a JupyterLab terminal):

```bash
pip install "git+https://github.com/zqfang/jupyter-mcp-executor.git"
```

The extension enables itself on install; the package ships
`jupyter_server_config.d/jupyter_mcp_executor.json`.

## 2. Register the tools

The package doesn't register any tools by default, so you have to list them.
Put this in `/etc/jupyter/jupyter_server_config.py` (image-wide) or
`~/.jupyter/jupyter_server_config.py` (per user):

```python
c.MCPExtensionApp.mcp_tools = [
    "jupyter_mcp_executor.execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.execution_tools:setup_notebook",
    "jupyter_mcp_executor.execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.execution_tools:interrupt_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:restart_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:view_notebook",
    "jupyter_mcp_executor.execution_tools:add_code_cell",
    "jupyter_mcp_executor.execution_tools:edit_code_cell",
    "jupyter_mcp_executor.execution_tools:add_markdown_cell",
    "jupyter_mcp_executor.execution_tools:edit_markdown_cell",
    "jupyter_mcp_executor.execution_tools:read_cell",
    "jupyter_mcp_executor.execution_tools:view_cell_image",
    "jupyter_mcp_executor.execution_tools:execute_cell",
    "jupyter_mcp_executor.execution_tools:delete_cell",
]
```

The same list is in
[examples/jupyter_config_with_execution_tools.py](examples/jupyter_config_with_execution_tools.py).

> **Do not set `c.IdentityProvider.token` under JupyterHub.** The Hub already
> handles authentication; that setting is only for a standalone local server.

## 3. Restart the single-user server

Go to **File → Hub Control Panel → Stop My Server**, then start it again. The
server log prints the public MCP endpoint URL.

## 4. Create a Hub API token

Open `https://<hub-host>/hub/token` and create a token. By default a token you
create for yourself can reach your own server. If you restrict its scopes,
keep `access:servers!user=<username>`.

## 5. Connect an MCP client

Claude Code:

```bash
claude mcp add --transport http jupyter-hub \
  https://<hub-host>/user/<username>/mcp \
  --header "Authorization: Bearer <hub-api-token>"
```

If you use named servers, the URL is
`https://<hub-host>/user/<username>/<servername>/mcp`.

### Check the endpoint with curl

```bash
curl -X POST https://<hub-host>/user/<username>/mcp \
  -H "Authorization: Bearer <hub-api-token>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

A successful response contains the server's `initialize` result.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `403` | Token missing or wrong, or it lacks the `access:servers` scope, or a custom authorizer denies `execute` on `kernels` or `read`/`write` on `contents` |
| `404` or a redirect to the Hub | Server not running, or the package isn't installed in the single-user environment |
| Connected but no tools listed | `mcp_tools` isn't set in a config file the single-user server reads |
| Tools work but nothing appears live in JupyterLab | The notebook isn't open in JupyterLab; closed notebooks are edited on disk (expected) |

## Security notes

- Whoever holds the token can run code as you on your server. Treat it like a
  password.
- Send the token in the `Authorization` header, never as `?token=` in the URL;
  URLs end up in logs and shell history.
- Each user's endpoint can only reach that user's own kernels and files.
- If another extension already serves `/mcp`, move the endpoint with
  `c.MCPExtensionApp.mcp_endpoint_path`; the URL becomes
  `/user/<username>/<mcp_endpoint_path>`.
