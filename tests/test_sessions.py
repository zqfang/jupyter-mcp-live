# test_sessions.py
import asyncio
import sys
sys.path.insert(0, '/Users/fangzq/Github/jupyter-mcp-executor')

from jupyter_mcp_executor.notebook_execution_tools import (
    list_notebook_sessions,
    set_server_context
)

async def main():
    # Note: You need an actual Jupyter server context
    # This will only work when running within Jupyter server
    try:
        sessions = await list_notebook_sessions()
        print(f"Found {len(sessions)} active sessions:")
        for session in sessions:
            print(f"\nNotebook: {session['path']}")
            print(f"  Kernel ID: {session['kernel']['id']}")
            print(f"  Kernel Name: {session['kernel']['name']}")
            print(f"  State: {session['kernel'].get('execution_state', 'unknown')}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())