"""The parts of the async HTTP client that need no server: the response, the
thread-backed transport, and the client over a fake transport."""

from __future__ import annotations

import asyncio
import errno
import gzip
import threading

import pytest

import pathapi.http as http
from pathapi.http import AsyncHTTPClient, AsyncHTTPResponse, ThreadedTransport
from transport_fakes import FakeAsyncTransport, FakeTransport, areply, reply


def run(coroutine):
    return asyncio.run(coroutine)


# --- the response -------------------------------------------------------------


class _Pieces:
    """A body of the pieces given, read on the loop, that notes its closing."""

    def __init__(self, *pieces):
        self.pieces = list(pieces)
        self.closed = 0

    async def read(self, amount):
        return self.pieces.pop(0) if self.pieces else b""

    async def aclose(self):
        self.closed += 1


def test_a_response_built_from_bytes_reads_whole_in_parts_and_after_closing():
    async def scenario():
        response = AsyncHTTPResponse(200, "OK", {"A": "1"}, "http://h/", b"abcdef")
        assert (response.status, response.reason) == (200, "OK")
        assert response.headers["a"] == "1" and response.url == "http://h/"
        assert await response.read(2) == b"ab"
        assert [part async for part in response.iter_bytes(3)] == [b"cde", b"f"]
        await response.aclose()
        assert await response.read() == b""
        again = AsyncHTTPResponse(200, "OK", {}, "u", b"kept")
        await again.aclose()
        assert await again.read() == b"kept"

    run(scenario())


def test_a_response_decodes_only_what_its_headers_say():
    packed = gzip.compress(b"hello")

    async def scenario():
        headers = {"Content-Encoding": "gzip"}
        decoded = AsyncHTTPResponse(200, "", headers, "u", packed)
        assert await decoded.read() == b"hello"
        left = AsyncHTTPResponse(200, "", headers, "u", packed, decode=False)
        assert await left.read() == packed
        plain = AsyncHTTPResponse(200, "", {}, "u", packed)
        assert await plain.read() == packed

    run(scenario())


def test_a_streamed_response_closes_its_body_once_and_refuses_reads_after():
    async def scenario():
        body = _Pieces(b"abc", b"def")
        async with AsyncHTTPResponse(200, "", {}, "u", body) as response:
            assert await response.read(4) == b"abcd"
        await response.aclose()
        assert body.closed == 1
        with pytest.raises(ValueError):
            await response.read(1)

    run(scenario())


def test_buffering_reads_the_rest_closes_the_body_and_keeps_it_readable():
    async def scenario():
        body = _Pieces(b"abc", b"def")
        response = AsyncHTTPResponse(200, "", {}, "u", body)
        assert await response.buffered() is response
        assert body.closed == 1
        assert await response.read() == b"abcdef"

    run(scenario())


def test_a_body_over_the_limit_is_efbig_and_closes_the_body():
    async def scenario():
        body = _Pieces(b"x" * 10, b"y" * 10)
        response = AsyncHTTPResponse(200, "", {}, "u", body)
        with pytest.raises(OSError) as excinfo:
            await response._buffered(5, "too long")
        assert excinfo.value.errno == errno.EFBIG and "too long" in str(excinfo.value)
        assert body.closed == 1

    run(scenario())


def test_a_coded_body_that_ends_early_is_an_io_error():
    packed = gzip.compress(b"hello world" * 100)[:-8]

    async def scenario():
        response = AsyncHTTPResponse(200, "", {"Content-Encoding": "gzip"}, "u", packed)
        with pytest.raises(OSError) as excinfo:
            await response.read()
        assert excinfo.value.errno == errno.EIO

    run(scenario())


# --- the thread-backed transport ----------------------------------------------


class _Streamed:
    """A synchronous response whose body reports the thread that reads it."""

    def __init__(self, threads):
        self.threads = threads
        self.closed = 0
        self.made = []  # kept, so that a response is never closed by being collected

    def make(self):
        outer = self

        class Source:
            def __init__(self):
                self.parts = [b"one", b"two"]

            def read(self, amount):
                outer.threads.append(threading.get_ident())
                return self.parts.pop(0) if self.parts else b""

            def close(self):
                outer.closed += 1

        response = http.HTTPResponse(200, "OK", {}, "http://h/x", Source())
        self.made.append(response)
        return response


class _Sync:
    def __init__(self, threads, streamed):
        self.threads = threads
        self.streamed = streamed

    def send(self, method, url, *, headers, body=None, timeout, stream=False):
        self.threads.append(threading.get_ident())
        response = self.streamed.make()
        return response if stream else response.buffered()

    def close(self):
        pass


def test_a_request_and_the_reading_of_its_body_run_off_the_event_loop_thread():
    threads = []
    streamed = _Streamed(threads)

    async def scenario():
        transport = ThreadedTransport(_Sync(threads, streamed))
        loop_thread = threading.get_ident()
        reply_ = await transport.send(
            "GET", "http://h/x", headers={}, timeout=1, stream=True
        )
        assert await reply_.read() == b"onetwo"
        await reply_.aclose()
        assert streamed.closed == 1
        assert threads and loop_thread not in threads

    run(scenario())


def test_a_buffered_request_returns_a_body_that_is_already_read():
    threads = []
    streamed = _Streamed(threads)

    async def scenario():
        transport = ThreadedTransport(_Sync(threads, streamed))
        response = await transport.send("GET", "http://h/x", headers={}, timeout=1)
        assert streamed.closed == 1
        threads.clear()
        assert await response.read() == b"onetwo"
        assert threads == []

    run(scenario())


def test_the_arguments_and_the_errors_of_the_synchronous_transport_come_through():
    sync = FakeTransport(default=reply(200))

    async def scenario():
        transport = ThreadedTransport(sync)
        body = b"data"
        await transport.send(
            "PUT", "http://h/x", headers={"A": "1"}, body=body, timeout=(1, 2)
        )
        sent = sync.sent[0]
        assert (sent.method, sent.url, sent.headers, sent.body) == (
            "PUT",
            "http://h/x",
            {"A": "1"},
            body,
        )
        assert sent.timeout == (1, 2) and sent.stream is False
        sync.fail = ConnectionRefusedError("refused")
        with pytest.raises(ConnectionRefusedError):
            await transport.send("GET", "http://h/x", headers={}, timeout=1)

    run(scenario())


def test_a_transport_given_stays_open_and_one_it_built_is_closed(monkeypatch):
    built = FakeTransport(default=reply(200))
    monkeypatch.setattr(
        "pathapi.http._async_transports.default_transport", lambda: built
    )
    given = FakeTransport(default=reply(200))

    async def scenario():
        await ThreadedTransport(given).aclose()
        assert given.closed == 0
        transport = ThreadedTransport()
        assert transport.transport is built
        await transport.aclose()
        await transport.aclose()
        assert built.closed == 1

    run(scenario())


def test_a_caller_cancelled_while_a_request_is_sent_has_its_reply_closed():
    arrived = threading.Event()
    release = threading.Event()
    streamed = _Streamed([])

    class Slow(_Sync):
        def send(self, method, url, *, headers, body=None, timeout, stream=False):
            arrived.set()
            release.wait(10)
            return super().send(
                method, url, headers=headers, body=body, timeout=timeout, stream=stream
            )

    async def scenario():
        transport = ThreadedTransport(Slow([], streamed))
        task = asyncio.ensure_future(
            transport.send("GET", "http://h/x", headers={}, timeout=1, stream=True)
        )
        await asyncio.get_running_loop().run_in_executor(None, arrived.wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        for _ in range(500):
            if streamed.closed:
                break
            await asyncio.sleep(0.01)
        assert streamed.closed == 1

    run(scenario())


def test_a_transport_is_an_object_with_a_send_method():
    with pytest.raises(TypeError):
        ThreadedTransport(object())


# --- the client over a fake async transport -----------------------------------------


def test_a_streamed_body_is_not_read_once_the_deadline_has_passed():
    class Counted(_Pieces):
        async def read(self, amount):
            self.reads = getattr(self, "reads", 0) + 1
            return await super().read(amount)

    body = Counted(b"first", b"second")

    async def scenario():
        transport = FakeAsyncTransport(
            default=AsyncHTTPResponse(200, "OK", {}, "http://h/x", body)
        )
        client = AsyncHTTPClient(transport, deadline=0.2)
        reply_ = await client.request("GET", "http://h/x", stream=True)
        assert await reply_.read(5) == b"first"
        await asyncio.sleep(0.3)
        with pytest.raises(TimeoutError):
            await reply_.read(1)
        assert body.reads == 1
        await reply_.aclose()

    run(scenario())


def test_every_request_carries_the_user_agent_accept_and_merged_headers():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        client = AsyncHTTPClient(
            transport, headers={"accept": "text/html", "X-A": "client"}
        )
        await client.request("GET", "http://h/x", headers={"x-a": "call", "X-B": "b"})
        sent = transport.sent[0].headers
        assert {k.lower(): v for k, v in sent.items()} == {
            "user-agent": sent["User-Agent"],
            "accept": "text/html",
            "x-a": "call",
            "x-b": "b",
        }
        assert sent["User-Agent"].startswith("pathapi/")

    run(scenario())


def test_credentials_given_as_auth_are_sent_and_a_str_is_the_value():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        client = AsyncHTTPClient(transport)
        await client.request("GET", "http://h/x", auth=("al@ice", "s3cr3t"))
        await client.request("GET", "http://h/x", auth="Bearer T")
        assert transport.sent[0].headers["Authorization"].startswith("Basic ")
        assert transport.sent[1].headers["Authorization"] == "Bearer T"

    run(scenario())


def test_the_timeout_of_the_client_is_sent_and_a_call_may_replace_it():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        client = AsyncHTTPClient(transport, timeout=7)
        await client.request("GET", "http://h/x")
        await client.request("GET", "http://h/x", timeout=(1, 2))
        await client.request("GET", "http://h/x", timeout=None)
        assert [s.timeout for s in transport.sent] == [7, (1, 2), None]
        assert all(s.stream for s in transport.sent)

    run(scenario())


def test_a_deadline_that_is_not_positive_is_a_value_error():
    with pytest.raises(ValueError):
        AsyncHTTPClient(FakeAsyncTransport(), deadline=0)
    with pytest.raises(TypeError):
        AsyncHTTPClient(FakeAsyncTransport(), deadline="soon")


def test_each_wait_is_capped_at_the_time_that_remains():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        client = AsyncHTTPClient(transport, deadline=5)
        await client.request("GET", "http://h/x", timeout=(60, 60))
        connect, read = transport.sent[0].timeout
        assert 0 < connect <= 5 and 0 < read <= 5

    run(scenario())


def test_a_chain_of_redirects_shares_one_deadline():
    class Slow(FakeAsyncTransport):
        async def send(self, *args, **kwargs):
            await asyncio.sleep(0.15)
            return await super().send(*args, **kwargs)

    async def scenario():
        transport = Slow(
            {
                ("GET", "http://h/a"): areply(302, headers={"Location": "/b"}),
                ("GET", "http://h/b"): areply(302, headers={"Location": "/c"}),
            },
            default=areply(200),
        )
        client = AsyncHTTPClient(transport, deadline=0.2)
        with pytest.raises(TimeoutError):
            await client.request("GET", "http://h/a")

    run(scenario())


def test_a_url_or_header_with_a_control_character_is_refused_before_any_send():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        client = AsyncHTTPClient(transport)
        with pytest.raises(ValueError):
            await client.request("GET", "http://h/x\r\nHost: evil")
        with pytest.raises(ValueError):
            await client.request("GET", "http://h/x", headers={"X": "a\r\nb"})
        assert transport.sent == []

    run(scenario())


def test_a_str_body_is_sent_as_utf_8():
    async def scenario():
        transport = FakeAsyncTransport(default=areply(200))
        await AsyncHTTPClient(transport).request("PUT", "http://h/x", data="é")
        assert transport.sent[0].body == "é".encode("utf-8")

    run(scenario())


def test_the_loop_is_left_as_it_was():
    async def scenario():
        loop = asyncio.get_running_loop()
        await AsyncHTTPClient(FakeAsyncTransport(default=areply(200))).request(
            "GET", "http://h/x"
        )
        assert asyncio.get_running_loop() is loop and not loop.is_closed()

    run(scenario())
