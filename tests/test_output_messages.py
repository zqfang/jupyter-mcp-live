"""Kernel output messages beyond plain appends: stream merging, clear_output,
update_display_data, and the per-response cap on the number of outputs.

The notebook and the MCP response must show what JupyterLab would show for
the same messages: a progress loop that clears and redraws its output leaves
one frame, not thousands, and a display updated by display_id shows its
latest content.
"""

import asyncio
import random
import time
from queue import Empty

import pytest
from jupyter_ydoc import YNotebook

from jupyter_mcp_executor.execution_engine import _CellOutputStreamer
from jupyter_mcp_executor.execution_progress import ExecutionProgress
from jupyter_mcp_executor.execution_utils import _CellOutputs, collect_outputs
from jupyter_mcp_executor.notebook_config import OUTPUT_FILTER_CONFIG
from jupyter_mcp_executor.notebook_models import CellContent
from jupyter_mcp_executor.output_filter import OutputFilter
from jupyter_mcp_executor.ydoc_adapter import YDocAdapter

MSG_ID = "exec-1"


def _msg(msg_type, content, msg_id=MSG_ID):
    return {
        "header": {"msg_type": msg_type},
        "parent_header": {"msg_id": msg_id},
        "content": content,
    }


def _stream(text, name="stdout"):
    return _msg("stream", {"name": name, "text": text})


def _display(text, display_id=None):
    content = {"data": {"text/plain": text}, "metadata": {}}
    if display_id is not None:
        content["transient"] = {"display_id": display_id}
    return _msg("display_data", content)


def _update(text, display_id):
    return _msg(
        "update_display_data",
        {
            "data": {"text/plain": text},
            "metadata": {},
            "transient": {"display_id": display_id},
        },
    )


def _clear(wait=False):
    return _msg("clear_output", {"wait": wait})


IDLE = _msg("status", {"execution_state": "idle"})


class ScriptedClient:
    """Kernel client whose iopub channel replays the given messages."""

    def __init__(self, messages):
        self._iopub = list(messages)

    async def get_iopub_msg(self, timeout=None):
        if self._iopub:
            return self._iopub.pop(0)
        raise Empty


class Recorder:
    """Records the live-streaming callbacks."""

    def __init__(self):
        self.appended = []
        self.resets = []

    async def on_output(self, output):
        self.appended.append(output)

    async def on_reset(self, outputs):
        self.resets.append(outputs)


async def _collect(messages, recorder=None):
    deadline = asyncio.get_running_loop().time() + 5
    callbacks = (
        {"on_output": recorder.on_output, "on_reset": recorder.on_reset}
        if recorder
        else {}
    )
    outputs, status, error = await collect_outputs(
        ScriptedClient([*messages, IDLE]), MSG_ID, None, deadline, **callbacks
    )
    assert (status, error) == ("ok", None)
    return outputs


def _texts(outputs):
    return [
        o.get("text") if o["output_type"] == "stream" else o["data"]["text/plain"]
        for o in outputs
    ]


@pytest.mark.asyncio
async def test_consecutive_stream_chunks_become_one_output():
    """Each print is its own iopub message; JupyterLab merges them."""
    recorder = Recorder()
    outputs = await _collect(
        [_stream("a\n"), _stream("b\n"), _stream("err\n", "stderr"), _stream("c\n")],
        recorder,
    )

    assert [(o["name"], o["text"]) for o in outputs] == [
        ("stdout", "a\nb\n"),
        ("stderr", "err\n"),
        ("stdout", "c\n"),
    ]
    # Live streaming still receives every chunk as it arrives.
    assert [o["text"] for o in recorder.appended] == ["a\n", "b\n", "err\n", "c\n"]


@pytest.mark.asyncio
async def test_clear_output_clears_immediately():
    recorder = Recorder()
    outputs = await _collect([_stream("old\n"), _clear(), _stream("new\n")], recorder)

    assert _texts(outputs) == ["new\n"]
    assert recorder.resets == [[]]


@pytest.mark.asyncio
async def test_clear_output_wait_clears_when_the_next_output_arrives():
    """clear_output(wait=True) keeps the old frame until the new one is ready,
    then the cell shows only the new one (one write, no empty flash)."""
    recorder = Recorder()
    outputs = await _collect(
        [_display("frame 1"), _clear(wait=True), _display("frame 2")], recorder
    )

    assert _texts(outputs) == ["frame 2"]
    assert [_texts(r) for r in recorder.resets] == [["frame 2"]]
    assert _texts(recorder.appended) == ["frame 1"]


@pytest.mark.asyncio
async def test_clear_output_wait_without_new_output_keeps_the_last_frame():
    outputs = await _collect([_display("frame 1"), _clear(wait=True)])

    assert _texts(outputs) == ["frame 1"]


@pytest.mark.asyncio
async def test_progress_loop_leaves_only_its_last_frame():
    """The tqdm / live-plot pattern: clear(wait=True) + redraw, many times."""
    frames = []
    for i in range(500):
        frames += [_clear(wait=True), _stream(f"step {i}\n")]

    outputs = await _collect(frames)

    assert _texts(outputs) == ["step 499\n"]


@pytest.mark.asyncio
async def test_update_display_data_updates_the_display_with_that_id():
    recorder = Recorder()
    outputs = await _collect(
        [
            _display("v1", display_id="d1"),
            _stream("log\n"),
            _display("other", display_id="d2"),
            _update("v2", display_id="d1"),
        ],
        recorder,
    )

    assert _texts(outputs) == ["v2", "log\n", "other"]
    assert [_texts(r) for r in recorder.resets] == [["v2", "log\n", "other"]]


@pytest.mark.asyncio
async def test_update_of_an_unknown_display_is_ignored():
    """The display may belong to another cell; this cell's outputs stay."""
    recorder = Recorder()
    outputs = await _collect([_display("mine"), _update("x", "elsewhere")], recorder)

    assert _texts(outputs) == ["mine"]
    assert recorder.resets == []


@pytest.mark.asyncio
async def test_cleared_displays_are_not_updated_afterwards():
    outputs = await _collect(
        [_display("v1", display_id="d1"), _clear(), _update("v2", "d1")]
    )

    assert outputs == []


@pytest.mark.asyncio
async def test_streamer_rewrites_the_cell_on_reset():
    """A clear or display update replaces the cell's outputs in the notebook."""

    class Adapter:
        def __init__(self):
            self.updated = []

        async def find_cell_by_id(self, cell_id):
            return (None, 3)

        async def update_cell_outputs(self, position, execution_count, outputs):
            self.updated.append((position, execution_count, outputs))

    adapter = Adapter()
    streamer = _CellOutputStreamer(adapter, "cell-1", 3)
    new = [{"output_type": "stream", "name": "stdout", "text": "step 9\n"}]

    await streamer.on_reset(new)

    assert adapter.updated == [(3, None, new)]
    assert streamer.complete


def test_response_keeps_the_first_and_last_outputs_of_a_long_list():
    """A cell with thousands of outputs must not flood the model's context."""
    limit = OUTPUT_FILTER_CONFIG.max_outputs
    outputs = [
        {"output_type": "display_data", "data": {"text/plain": str(i)}, "metadata": {}}
        for i in range(limit * 10)
    ]

    filtered = OutputFilter.filter_outputs(outputs)

    assert len(filtered) == limit + 1
    head = limit // 2
    assert _texts(filtered[:head]) == [str(i) for i in range(head)]
    tail = limit - head
    assert _texts(filtered[-tail:]) == [
        str(i) for i in range(limit * 10 - tail, limit * 10)
    ]
    marker = filtered[head]["data"]["text/plain"]
    omitted = limit * 10 - limit
    assert f"outputs {head}-{head + omitted - 1} ({omitted}) omitted" in marker
    assert outputs[head]["data"]["text/plain"] == str(head)  # input not mutated


def test_response_under_the_cap_is_unchanged():
    outputs = [{"output_type": "stream", "name": "stdout", "text": "x\n"}] * 3

    assert OutputFilter.filter_outputs(outputs) == outputs


@pytest.mark.asyncio
async def test_merging_many_chunks_stays_linear():
    """Each print is a message; re-joining all text per chunk was quadratic
    (6.6 s of event-loop time for 100k log lines)."""
    line = "a typical log line of about sixty characters, e.g. step 123\n"
    cell = _CellOutputs(None, None)
    start = time.perf_counter()
    for _ in range(100_000):
        await cell.add({"output_type": "stream", "name": "stdout", "text": line}, None)
    elapsed = time.perf_counter() - start

    assert cell.outputs[0]["text"] == line * 100_000
    assert elapsed < 2


@pytest.mark.asyncio
async def test_stream_text_given_as_a_list_is_joined():
    outputs = await _collect(
        [_msg("stream", {"name": "stdout", "text": ["a", "b\n"]}), _stream("c\n")]
    )

    assert _texts(outputs) == ["ab\nc\n"]


@pytest.mark.asyncio
async def test_progress_counts_outputs_that_replace_the_cell():
    """A redrawn frame reaches progress too, not only appended outputs."""
    reported = []

    async def report(progress, total, message):
        reported.append(message)

    progress = ExecutionProgress(report, "nb.ipynb cell 0")
    progress.mark_running()

    await progress.on_reset(
        [{"output_type": "stream", "name": "stdout", "text": "step 9\n"}]
    )
    await progress.on_reset([])  # clear_output: nothing new to report
    await progress.send()  # past the throttle

    assert reported
    assert reported[-1].endswith("1 output · step 9")


def test_response_shows_the_last_carriage_return_frame():
    """tqdm writing to stderr redraws with \\r; the response shows the final
    frame of each line, as a terminal would. The notebook keeps the text."""
    output = {
        "output_type": "stream",
        "name": "stderr",
        "text": "\r 10%|#   |\r 50%|##  |\r100%|####|\nnext line\r\n",
    }

    filtered = OutputFilter.filter_outputs([output])

    assert filtered[0]["text"] == "100%|####|\nnext line\n"
    assert output["text"].startswith("\r 10%")


async def _run_on_ydoc(steps):
    """Feed steps to _CellOutputs streaming into a real YNotebook cell."""
    adapter = YDocAdapter(YNotebook(), "/tmp/x.ipynb")
    position, _ = await adapter.add_cell(CellContent(source="x", cell_type="code"))

    async def on_output(output):
        await adapter.append_cell_output(position, output)

    async def on_reset(outputs):
        await adapter.update_cell_outputs(position, None, outputs)

    cell = _CellOutputs(on_output, on_reset)
    for n, step in enumerate(steps):
        if step == "out":
            await cell.add(
                {"output_type": "stream", "name": "stdout", "text": f"o{n}\n"}, None
            )
        elif step == "err":
            await cell.add(
                {"output_type": "stream", "name": "stderr", "text": f"e{n}\n"}, None
            )
        elif step == "display":
            data = {"text/plain": f"d{n}"}
            await cell.add(
                {"output_type": "display_data", "data": data, "metadata": {}}, "id1"
            )
        elif step == "update":
            await cell.update("id1", {"text/plain": f"u{n}"}, {})
        elif step == "clear":
            await cell.clear(wait=False)
        elif step == "clear_wait":
            await cell.clear(wait=True)
        elif step == "error":
            error = {
                "output_type": "error",
                "ename": "E",
                "evalue": "v",
                "traceback": [],
            }
            await cell.add(error, None)
    stored = (await adapter.read_cell(position)).outputs
    return stored, cell.outputs


def _visible(outputs):
    return [
        (
            o["output_type"],
            o.get("text") or o.get("data", {}).get("text/plain") or o.get("ename"),
        )
        for o in outputs
    ]


@pytest.mark.asyncio
async def test_live_notebook_matches_the_returned_outputs():
    """After a run whose outputs streamed completely, only the execution count
    is written, so the streamed YDoc must equal the returned list."""
    rng = random.Random(1)
    steps = ["out", "err", "display", "update", "clear", "clear_wait", "error"]
    for _ in range(300):
        sequence = [rng.choice(steps) for _ in range(rng.randint(1, 10))]
        stored, returned = await _run_on_ydoc(sequence)
        assert _visible(stored) == _visible(returned), sequence
