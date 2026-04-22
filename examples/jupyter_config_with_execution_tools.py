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
    "jupyter_mcp_executor.notebook_execution_tools:list_notebook_sessions",
    "jupyter_mcp_executor.notebook_execution_tools:list_notebook_kernels",
    "jupyter_mcp_executor.notebook_execution_tools:execute_notebook_code",
    "jupyter_mcp_executor.notebook_execution_tools:setup_notebook",
    "jupyter_mcp_executor.notebook_execution_tools:switch_notebook_kernel",
    "jupyter_mcp_executor.notebook_execution_tools:shutdown_notebook",
    "jupyter_mcp_executor.notebook_execution_tools:modify_notebook_cells",
    "jupyter_mcp_executor.notebook_execution_tools:query_notebook",
    "jupyter_mcp_executor.notebook_execution_tools:read_cell",

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
# Tool Descriptions
# ============================================================================

# list_running_kernels: Lists all currently running Jupyter kernels
#   - Returns kernel IDs, names, states, and connection counts
#   - Useful for discovering available kernels before execution
#
# execute_cell: Executes Python code in a Jupyter kernel
#   - Can use existing kernel or start a new one
#   - Returns outputs, execution count, and any errors
#   - Supports timeout configuration
#   - Perfect for running generated code snippets
#
# execute_notebook: Executes all cells in a notebook file
#   - Runs entire notebook from start to finish
#   - Saves executed notebook with outputs
#   - Useful for batch processing or testing notebooks
#
# shutdown_kernel: Shuts down a running kernel
#   - Cleans up kernel resources
#   - Use after completing code execution tasks

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
c.ServerApp.port = 8888

# Allow cross-origin requests (if accessing from different origins)
c.ServerApp.allow_origin = '*'

# Set a token for authentication
c.IdentityProvider.token = 'BLOCK'

# Enable debug logging for troubleshooting
c.MCPExtensionApp.log_level = 'INFO'

# Set Jupyter root directory to current folder
c.ServerApp.root_dir = "."

# Enable autoreload for development
c.ServerApp.autoreload = True  
# Set Jupyter log level to INFO
c.ServerApp.log_level = 'INFO'
