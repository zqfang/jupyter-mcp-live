"""Output messages from a real ipykernel end up as JupyterLab would show them."""

from jupyter_mcp_live.execution_engine import ExecutionEngine
from jupyter_mcp_live.server_context import set_server_context

PROGRESS_AND_DISPLAY = """
from IPython.display import clear_output, display
for i in range(50):
    clear_output(wait=True)
    print(f"step {i}")
handle = display("v1", display_id=True)
handle.update("v2")
print("done")
print("really done")
"""


async def test_progress_loop_and_display_update(jp_serverapp):
    set_server_context(jp_serverapp)
    kernels = jp_serverapp.kernel_manager
    kernel_id = await kernels.start_kernel(kernel_name="python3")
    engine = ExecutionEngine()
    try:
        result = await engine.execute_cell_code(
            PROGRESS_AND_DISPLAY, kernel_id=kernel_id, timeout=60
        )
    finally:
        engine.forget_kernel_client(kernel_id)
        await kernels.shutdown_kernel(kernel_id)

    assert result.status == "ok", result.error
    assert [
        (o["output_type"], o.get("text") or o["data"]["text/plain"])
        for o in result.outputs
    ] == [
        ("stream", "step 49\n"),
        ("display_data", "'v2'"),
        ("stream", "done\nreally done\n"),
    ]
