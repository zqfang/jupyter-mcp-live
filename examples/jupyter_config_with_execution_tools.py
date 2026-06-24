"""Example Jupyter configuration for MCP server with notebook execution tools.

This configuration demonstrates how to set up the Jupyter Server MCP extension
with the new notebook execution tools for generating and running code.

Usage:
    jupyter lab --config=examples/jupyter_config_with_execution_tools.py

The MCP server will be available at: http://localhost:8080/mcp
"""

c = get_config()  # noqa: F821

# ============================================================================
# MCP Server Basic Configuration
# ============================================================================

# Name for the MCP server (shown to MCP clients)
c.MCPExtensionApp.mcp_name = "Jupyter Code Execution MCP Server"

# Port for the MCP server to listen on
c.MCPExtensionApp.mcp_port = 8080

# ============================================================================
# Register Notebook Execution Tools
# ============================================================================

c.MCPExtensionApp.mcp_tools = [
    # Notebook execution and kernel management tools
    "jupyter_mcp_executor.execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.execution_tools:setup_notebook",
    "jupyter_mcp_executor.execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.execution_tools:view_notebook",
    "jupyter_mcp_executor.execution_tools:add_code_cell",
    "jupyter_mcp_executor.execution_tools:edit_code_cell",
    "jupyter_mcp_executor.execution_tools:add_markdown_cell",
    "jupyter_mcp_executor.execution_tools:edit_markdown_cell",
    "jupyter_mcp_executor.execution_tools:read_cell",
    "jupyter_mcp_executor.execution_tools:execute_cell",
    "jupyter_mcp_executor.execution_tools:delete_cell",

    # Optional: Add file system tools from jupyter-ai-tools (if installed)
    # "jupyter_ai_tools.toolkits.file_system:read",
    # "jupyter_ai_tools.toolkits.file_system:write", 
    # "jupyter_ai_tools.toolkits.file_system:edit",
    # "jupyter_ai_tools.toolkits.file_system:ls",
    # "jupyter_ai_tools.toolkits.file_system:glob",

    # JupyterLab operations (jupyterlab-commands-toolkit)
    # "jupyterlab_commands_toolkit.tools:clear_all_outputs_in_notebook",
    # "jupyterlab_commands_toolkit.tools:open_document",
    # "jupyterlab_commands_toolkit.tools:open_markdown_file_in_preview_mode",
    # "jupyterlab_commands_toolkit.tools:show_diff_of_current_notebook",
    
    # Utility functions  
    # "os:getcwd",
    # "json:dumps",
    # "time:time",
    # "platform:system",
]

# ============================================================================
# Example Usage with Claude Code
# ============================================================================

# Add this to your Claude Code MCP configuration:
#
# "mcpServers": {
#   "jupyter-mcp": {
#     "type": "http",
#     "url": "http://localhost:8080/mcp"
#   }
# }
#
# Or use the claude CLI:
#   claude mcp add --transport http jupyter-mcp http://localhost:8080/mcp

# ============================================================================
# Example Usage with Gemini CLI
# ============================================================================

# Add this to ~/.gemini/settings.json:
#
# {
#   "mcpServers": {
#     "jupyter-mcp": {
#       "httpUrl": "http://localhost:8080/mcp"
#     }
#   }
# }

# ============================================================================
# Security Notes
# ============================================================================

# WARNING: These tools allow execution of arbitrary code on your system.
# Only use this MCP server:
#   1. On your local machine
#   2. With trusted AI assistants
#   3. Behind a firewall (do NOT expose to the internet)
#   4. In development/testing environments
#
# Consider the security implications before deploying in production.

# ============================================================================
# Optional: Jupyter Server Configuration
# ============================================================================

# Allow connections from any IP (useful for remote access)
# WARNING: Only use this on trusted networks!
c.ServerApp.ip = '0.0.0.0'

# Disable browser auto-open (useful for headless servers)
c.ServerApp.open_browser = False

# Set a custom port for Jupyter Lab (separate from MCP port)
c.ServerApp.port = 8000

# Allow cross-origin requests (if accessing from different origins)
c.ServerApp.allow_origin = '*'

# Set a token for authentication
c.IdentityProvider.token = 'BLOCK'

# Enable debug logging for troubleshooting
c.MCPExtensionApp.log_level = 'INFO'

# Set Jupyter root directory to current folder
c.ServerApp.root_dir = "~/workspace"

# Enable autoreload for development
c.ServerApp.autoreload = False  
# Set Jupyter log level to INFO
c.ServerApp.log_level = 'INFO'
