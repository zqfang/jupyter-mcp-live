"""Tests for the in-process ASGI bridge used by the in-server /mcp endpoint."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from tornado.httputil import HTTPHeaders, HTTPServerRequest
from tornado.iostream import StreamClosedError

from jupyter_mcp_executor.asgi_bridge import EmbeddedASGIApp, run_asgi


class FakeHandler:
    """Records what the bridge does to a Tornado RequestHandler."""

    def __init__(self, request: HTTPServerRequest, fail_flush_after: int | None = None):
        self.request = request
        self.status: int | None = None
        self.headers: list[tuple[str, str]] = []
        self.chunks: list[bytes] = []
        self.flushes: list[int] = []  # len(chunks) at each flush
        self._fail_flush_after = fail_flush_after

    def set_status(self, status: int) -> None:
        self.status = status

    def set_header(self, name: str, value: str) -> None:
        self.headers = [(n, v) for n, v in self.headers if n.lower() != name.lower()]
        self.headers.append((name, value))

    def add_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def clear_header(self, name: str) -> None:
        self.headers = [(n, v) for n, v in self.headers if n.lower() != name.lower()]

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    async def flush(self) -> None:
        if (
            self._fail_flush_after is not None
            and len(self.flushes) >= self._fail_flush_after
        ):
            raise StreamClosedError()
        self.flushes.append(len(self.chunks))


def _request(
    method: str = "POST",
    uri: str = "/base/mcp?x=1",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> HTTPServerRequest:
    return HTTPServerRequest(
        method=method,
        uri=uri,
        headers=HTTPHeaders(headers or {}),
        body=body,
        host="example.org:8888",
    )


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "method": request.method,
            "path": request.url.path,
            "root_path": request.scope["root_path"],
            "scope_path": request.scope["path"],
            "query": request.url.query,
            "body": (await request.body()).decode(),
            "auth": request.headers.get("authorization"),
        },
        status_code=202,
        headers={"x-multi": "a"},
    )


def _app(routes) -> Starlette:
    return Starlette(routes=routes)


class TestRunAsgi:
    """run_asgi forwards a Tornado request to an ASGI app and back."""

    @pytest.mark.asyncio
    async def test_forwards_request_and_response(self):
        app = _app([Route("/mcp", _echo, methods=["POST"])])
        handler = FakeHandler(
            _request(body=b'{"a": 1}', headers={"Authorization": "token abc"})
        )

        await run_asgi(
            app, handler, path="/mcp", root_path="/base", disconnected=asyncio.Event()
        )

        assert handler.status == 202
        payload = b"".join(handler.chunks).decode()
        assert '"method":"POST"' in payload
        assert '"scope_path":"/base/mcp"' in payload
        assert '"root_path":"/base"' in payload
        assert '"query":"x=1"' in payload
        assert '"body":"{\\"a\\": 1}"' in payload
        assert '"auth":"token abc"' in payload
        assert ("x-multi", "a") in handler.headers

    @pytest.mark.asyncio
    async def test_routes_when_root_path_equals_app_path(self):
        """base_url "/mcp/" must not make Starlette strip the whole path."""
        app = _app([Route("/mcp", _echo, methods=["POST"])])
        handler = FakeHandler(_request(uri="/mcp/mcp"))

        await run_asgi(
            app, handler, path="/mcp", root_path="/mcp", disconnected=asyncio.Event()
        )

        assert handler.status == 202

    @pytest.mark.asyncio
    async def test_app_returning_without_response_raises(self):
        async def silent(_scope, _receive, _send):
            return None

        handler = FakeHandler(_request())

        with pytest.raises(RuntimeError, match="without starting a response"):
            await run_asgi(
                silent, handler, path="/mcp", root_path="", disconnected=asyncio.Event()
            )

    @pytest.mark.asyncio
    async def test_body_before_response_start_raises(self):
        async def body_first(_scope, _receive, send):
            await send({"type": "http.response.body", "body": b"x"})

        handler = FakeHandler(_request())

        with pytest.raises(RuntimeError, match=r"before http\.response\.start"):
            await run_asgi(
                body_first,
                handler,
                path="/mcp",
                root_path="",
                disconnected=asyncio.Event(),
            )
        assert handler.chunks == []

    @pytest.mark.asyncio
    async def test_repeated_response_headers_are_all_kept(self):
        async def cookies(_request: Request) -> JSONResponse:
            response = JSONResponse({})
            response.set_cookie("a", "1")
            response.set_cookie("b", "2")
            return response

        app = _app([Route("/mcp", cookies)])
        handler = FakeHandler(_request(method="GET"))

        await run_asgi(
            app, handler, path="/mcp", root_path="", disconnected=asyncio.Event()
        )

        set_cookies = [v for n, v in handler.headers if n.lower() == "set-cookie"]
        assert len(set_cookies) == 2

    @pytest.mark.asyncio
    async def test_streams_each_chunk_with_its_own_flush(self):
        async def events():
            for i in range(3):
                yield f"data: {i}\n\n".encode()

        async def sse(_request: Request) -> StreamingResponse:
            return StreamingResponse(events(), media_type="text/event-stream")

        app = _app([Route("/mcp", sse)])
        handler = FakeHandler(_request(method="GET"))

        await run_asgi(
            app, handler, path="/mcp", root_path="", disconnected=asyncio.Event()
        )

        body_chunks = [c for c in handler.chunks if c]
        assert body_chunks == [b"data: 0\n\n", b"data: 1\n\n", b"data: 2\n\n"]
        # Every non-empty chunk was flushed before the next one was written.
        for i in range(1, len(body_chunks) + 1):
            assert i in handler.flushes

    @pytest.mark.asyncio
    async def test_client_disconnect_is_delivered_to_the_app(self):
        seen = asyncio.Event()

        async def waits_for_disconnect(_scope, receive, _send):
            assert (await receive())["type"] == "http.request"
            message = await receive()
            assert message["type"] == "http.disconnect"
            seen.set()

        disconnected = asyncio.Event()
        handler = FakeHandler(_request(method="GET"))
        task = asyncio.create_task(
            run_asgi(
                waits_for_disconnect,
                handler,
                path="/mcp",
                root_path="",
                disconnected=disconnected,
            )
        )
        await asyncio.sleep(0)
        disconnected.set()
        await asyncio.wait_for(task, timeout=2)

        assert seen.is_set()

    @pytest.mark.asyncio
    async def test_closed_stream_marks_disconnected_and_stops_writing(self):
        async def endless(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for _ in range(5):
                await send(
                    {"type": "http.response.body", "body": b"x", "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        disconnected = asyncio.Event()
        # Flush 1 sends the headers, flush 2 the first chunk, flush 3 fails.
        handler = FakeHandler(_request(method="GET"), fail_flush_after=2)

        await run_asgi(
            endless, handler, path="/mcp", root_path="", disconnected=disconnected
        )

        assert disconnected.is_set()
        assert handler.chunks == [b"x", b"x"]

    @pytest.mark.asyncio
    async def test_app_error_before_response_propagates(self):
        async def broken(_scope, _receive, _send):
            msg = "boom"
            raise RuntimeError(msg)

        handler = FakeHandler(_request())

        with pytest.raises(RuntimeError, match="boom"):
            await run_asgi(
                broken, handler, path="/mcp", root_path="", disconnected=asyncio.Event()
            )


class TestEmbeddedASGIApp:
    """EmbeddedASGIApp runs the app's lifespan in one background task."""

    @staticmethod
    def _lifespan_app(events: list[str], fail: bool = False) -> Starlette:
        @asynccontextmanager
        async def lifespan(_app):
            events.append(("start", id(asyncio.current_task())))
            if fail:
                msg = "lifespan failed"
                raise RuntimeError(msg)
            yield
            events.append(("stop", id(asyncio.current_task())))

        return Starlette(lifespan=lifespan)

    @pytest.mark.asyncio
    async def test_start_and_stop_run_lifespan_in_one_task(self):
        events: list = []
        embedded = EmbeddedASGIApp(self._lifespan_app(events))

        await embedded.start()
        assert embedded.running
        await embedded.stop()

        assert not embedded.running
        assert [e[0] for e in events] == ["start", "stop"]
        assert events[0][1] == events[1][1]

    @pytest.mark.asyncio
    async def test_start_raises_when_lifespan_fails(self):
        embedded = EmbeddedASGIApp(self._lifespan_app([], fail=True))

        with pytest.raises(RuntimeError, match="lifespan failed"):
            await embedded.start()
        assert not embedded.running

    @pytest.mark.asyncio
    async def test_cancelled_start_does_not_leak_lifespan(self):
        events: list = []
        release = asyncio.Event()

        @asynccontextmanager
        async def slow_lifespan(_app):
            events.append("start")
            try:
                await release.wait()
                yield
            finally:
                events.append("cleanup")

        embedded = EmbeddedASGIApp(Starlette(lifespan=slow_lifespan))
        starting = asyncio.create_task(embedded.start())
        await asyncio.sleep(0.01)

        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting

        assert events == ["start", "cleanup"]
        assert not embedded.running
        # A later start works normally.
        release.set()
        await embedded.start()
        assert embedded.running
        await embedded.stop()

    @pytest.mark.asyncio
    async def test_stop_after_lifespan_crash_does_not_raise(self):
        crash = asyncio.Event()

        @asynccontextmanager
        async def crashing_lifespan(_app):
            yield
            await crash.wait()

        embedded = EmbeddedASGIApp(Starlette(lifespan=crashing_lifespan))
        await embedded.start()
        # Simulate the lifespan task dying while serving.
        embedded._task.cancel()
        await asyncio.sleep(0)

        await embedded.stop()

        assert not embedded.running

    @pytest.mark.asyncio
    async def test_stop_is_idempotent_and_safe_before_start(self):
        embedded = EmbeddedASGIApp(self._lifespan_app([]))

        await embedded.stop()
        await embedded.start()
        await embedded.stop()
        await embedded.stop()

        assert not embedded.running
