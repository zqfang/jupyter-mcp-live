"""Tests for MCP progress notifications during cell execution."""

import asyncio

import pytest

from jupyter_mcp_live.execution_progress import ExecutionProgress
from jupyter_mcp_live.notebook_config import ProgressConfig

# Long intervals so only the explicit clock drives throttling in sync tests.
QUIET = ProgressConfig(heartbeat_interval=3600, min_interval=1.0, message_max_chars=120)


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    """Collects (progress, total, message) like Context.report_progress."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, str]] = []

    async def __call__(self, progress: float, total: float | None, message: str):
        self.calls.append((progress, total, message))


def _stream(text: str) -> dict:
    return {"output_type": "stream", "name": "stdout", "text": text}


def _reporter(config=QUIET, label="nb.ipynb cell 3"):
    clock = FakeClock()
    recorder = Recorder()
    reporter = ExecutionProgress(recorder, label, config=config, clock=clock)
    reporter.mark_running()
    return reporter, recorder, clock


class TestThrottling:
    @pytest.mark.asyncio
    async def test_outputs_within_min_interval_send_once(self):
        reporter, recorder, clock = _reporter()

        clock.now += 1.5
        await reporter.on_output(_stream("tick 0\n"))
        clock.now += 0.2
        await reporter.on_output(_stream("tick 1\n"))
        clock.now += 0.2
        await reporter.on_output(_stream("tick 2\n"))

        assert len(recorder.calls) == 1

    @pytest.mark.asyncio
    async def test_next_output_after_min_interval_sends_again(self):
        reporter, recorder, clock = _reporter()

        clock.now += 1.5
        await reporter.on_output(_stream("tick 0\n"))
        clock.now += 1.0
        await reporter.on_output(_stream("tick 1\n"))

        assert len(recorder.calls) == 2

    @pytest.mark.asyncio
    async def test_output_right_after_start_is_not_sent(self):
        """Fast cells finish without any progress notification."""
        reporter, recorder, clock = _reporter()

        clock.now += 0.1
        await reporter.on_output(_stream("quick\n"))

        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_progress_strictly_increases_and_total_is_unknown(self):
        reporter, recorder, clock = _reporter()

        for _ in range(3):
            clock.now += 1.0
            await reporter.on_output(_stream("x\n"))

        progresses = [call[0] for call in recorder.calls]
        assert progresses == sorted(progresses)
        assert len(set(progresses)) == len(progresses)
        assert all(call[1] is None for call in recorder.calls)


class TestMessages:
    @pytest.mark.asyncio
    async def test_message_has_label_elapsed_count_and_last_line(self):
        reporter, recorder, clock = _reporter()

        clock.now += 12.4
        await reporter.on_output(_stream("loading\ntick 4\n\n"))

        message = recorder.calls[-1][2]
        assert message == "nb.ipynb cell 3 · 12s · 1 output · tick 4"

    @pytest.mark.asyncio
    async def test_output_kinds_are_summarized(self):
        cases = [
            (
                {"output_type": "error", "ename": "KeyError", "evalue": "'a'"},
                "KeyError: 'a'",
            ),
            (
                {"output_type": "display_data", "data": {"image/png": "..."}},
                "[image]",
            ),
            (
                {"output_type": "execute_result", "data": {"text/plain": "42"}},
                "[result]",
            ),
        ]
        for output, summary in cases:
            reporter, recorder, clock = _reporter()
            clock.now += 2
            await reporter.on_output(output)
            assert recorder.calls[-1][2].endswith(summary), output

    @pytest.mark.asyncio
    async def test_long_message_is_truncated(self):
        config = ProgressConfig(
            heartbeat_interval=3600, min_interval=1.0, message_max_chars=40
        )
        reporter, recorder, clock = _reporter(config=config)

        clock.now += 2
        await reporter.on_output(_stream("x" * 500 + "\n"))

        message = recorder.calls[-1][2]
        assert len(message) == 40
        assert message.endswith("…")

    @pytest.mark.asyncio
    async def test_waiting_for_kernel_until_marked_running(self):
        clock = FakeClock()
        recorder = Recorder()
        reporter = ExecutionProgress(
            recorder, "nb.ipynb cell 0", config=QUIET, clock=clock
        )

        clock.now += 3
        await reporter.send()

        assert recorder.calls[-1][2] == "nb.ipynb cell 0 · 3s · waiting for kernel"


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_silent_execution_gets_heartbeats(self):
        config = ProgressConfig(
            heartbeat_interval=0.05, min_interval=0.01, message_max_chars=120
        )
        recorder = Recorder()
        reporter = ExecutionProgress(recorder, "nb.ipynb cell 1", config=config)
        reporter.mark_running()

        async with reporter:
            await asyncio.sleep(0.18)

        assert len(recorder.calls) >= 2
        assert all("0 outputs" in call[2] for call in recorder.calls)

    @pytest.mark.asyncio
    async def test_heartbeat_stops_when_context_exits_on_error(self):
        config = ProgressConfig(
            heartbeat_interval=0.02, min_interval=0.01, message_max_chars=120
        )
        recorder = Recorder()
        reporter = ExecutionProgress(recorder, "nb.ipynb cell 1", config=config)

        async def failing_execution():
            async with reporter:
                await asyncio.sleep(0.05)
                msg = "kernel died"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="kernel died"):
            await failing_execution()

        sent = len(recorder.calls)
        await asyncio.sleep(0.08)
        assert len(recorder.calls) == sent


class TestRobustness:
    @pytest.mark.asyncio
    async def test_failing_report_disables_reporter_without_raising(self):
        calls = 0

        async def broken(_progress, _total, _message):
            nonlocal calls
            calls += 1
            msg = "client went away"
            raise ConnectionError(msg)

        clock = FakeClock()
        reporter = ExecutionProgress(broken, "nb", config=QUIET, clock=clock)
        reporter.mark_running()

        for _ in range(3):
            clock.now += 2
            await reporter.on_output(_stream("x\n"))

        assert calls == 1

    def test_from_request_without_context_returns_none(self):
        assert ExecutionProgress.from_request("nb.ipynb cell 0") is None
