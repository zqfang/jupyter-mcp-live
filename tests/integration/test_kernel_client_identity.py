"""The engine's kernel client must not share a ZMQ identity with other clients.

KernelManager.client() gives every client a clone of the manager's Session,
hence the same shell DEALER identity, and ipykernel enables ROUTER_HANDOVER:
the most recently connected socket with an identity receives its replies. Any
other server-side user of the kernel (an extension calling km.client()) then
took over our execute_reply messages.
"""

from jupyter_mcp_live.execution_engine import ExecutionEngine
from jupyter_mcp_live.server_context import set_server_context


async def test_reply_reaches_the_engine_while_another_client_shares_the_kernel(
    jp_serverapp,
):
    set_server_context(jp_serverapp)
    kernels = jp_serverapp.kernel_manager
    kernel_id = await kernels.start_kernel(kernel_name="python3")
    engine = ExecutionEngine()
    try:
        first = await engine.execute_cell_code(
            "x = 41", kernel_id=kernel_id, timeout=60
        )
        assert first.status == "ok"

        # Another server-side user of the kernel connects after us.
        rival = kernels.get_kernel(kernel_id).client()
        rival.start_channels()
        try:
            await rival.wait_for_ready(timeout=60)
            result = await engine.execute_cell_code(
                "x + 1", kernel_id=kernel_id, timeout=60
            )
        finally:
            rival.stop_channels()

        assert result.status == "ok", result.error
        assert result.execution_count == 2
        assert result.outputs[0]["data"]["text/plain"] == "42"
    finally:
        engine.forget_kernel_client(kernel_id)
        await kernels.shutdown_kernel(kernel_id)
