"""What an async transport puts on the wire and what it makes of the reply,
against loopback servers scripted at the byte level: the promises of
`test_transport_send.py` for a transport whose `send()` is a coroutine. Each
test runs once per async transport."""

import asyncio
import errno
import gzip
import threading
import zlib

import pytest

from pathapi.http import AsyncHTTPResponse
from transport_fakes import run_with_async_transport
from transport_wire import Drop, Hang, Reply, Wire, refusing_port, response

TIMEOUT = (5, 5)


async def send(transport, url, method="GET", headers=None, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    return await transport.send(method, url, headers=headers or {}, **kwargs)


def run(kind, scenario):
    return run_with_async_transport(kind, scenario)


# --- the request as the server receives it ------------------------------------


def test_request_line_host_and_headers_arrive_as_given(async_http_transport_kind):
    async def scenario(transport):
        reply = await send(
            transport, wire.url + "/a/b?x=1&y=%20", headers={"X-Probe": "1"}
        )
        assert await reply.read() == b"ok"

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
    (request,) = wire.requests
    assert (request.method, request.target) == ("GET", "/a/b?x=1&y=%20")
    assert request.header("Host") == f"127.0.0.1:{wire.port}"
    assert request.header("X-Probe") == "1"


def test_the_fragment_is_not_sent_and_an_empty_path_is_a_slash(
    async_http_transport_kind,
):
    async def scenario(transport):
        first = await send(transport, wire.url + "/p#frag")
        await first.read()
        second = await send(transport, f"http://127.0.0.1:{wire.port}")
        await second.read()
        return first.url, second.url

    with Wire() as wire:
        urls = run(async_http_transport_kind, scenario)
    assert [r.target for r in wire.requests] == ["/p", "/"]
    assert urls == (f"{wire.url}/p", f"{wire.url}/")


def test_gzip_and_deflate_are_advertised_unless_the_request_chooses(
    async_http_transport_kind,
):
    async def scenario(transport):
        await (await send(transport, wire.url + "/")).read()
        chosen = {"Accept-Encoding": "identity"}
        await (await send(transport, wire.url + "/", headers=chosen)).read()

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
    assert wire.requests[0].header("Accept-Encoding") == "gzip, deflate"
    assert wire.requests[1].header("Accept-Encoding") == "identity"


@pytest.mark.parametrize("method", ["PUT", "POST", "PATCH", "PROPFIND"])
def test_a_bytes_body_arrives_whole_with_its_length(async_http_transport_kind, method):
    async def scenario(transport):
        reply = await send(transport, wire.url + "/f", method=method, body=b"payload")
        await reply.read()

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
    request = wire.requests[0]
    assert request.method == method
    assert request.body == b"payload"
    assert request.header("Content-Length") == "7"


def test_a_put_without_a_body_says_so(async_http_transport_kind):
    async def scenario(transport):
        await (await send(transport, wire.url + "/f", method="PUT")).read()

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
    assert wire.requests[0].body == b""
    assert wire.requests[0].header("Content-Length") == "0"


def test_a_file_object_and_an_iterable_are_sent_whole(async_http_transport_kind):
    import io

    async def scenario(transport):
        body = io.BytesIO(b"from a file")
        await (await send(transport, wire.url + "/f", "PUT", body=body)).read()
        pieces = (piece for piece in (b"in ", b"pieces"))
        await (await send(transport, wire.url + "/g", "PUT", body=pieces)).read()

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
    assert [r.body for r in wire.requests] == [b"from a file", b"in pieces"]


def test_a_redirect_is_returned_and_not_followed(async_http_transport_kind):
    def script(request):
        return response(302, {"Location": "/elsewhere"}, reason="Found")

    async def scenario(transport):
        reply = await send(transport, wire.url + "/start")
        assert (reply.status, reply.headers["location"]) == (302, "/elsewhere")
        await reply.aclose()

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)
    assert [r.target for r in wire.requests] == ["/start"]


# --- the reply ------------------------------------------------------------------


def test_status_reason_and_case_blind_read_only_headers(async_http_transport_kind):
    def script(request):
        return (
            b"HTTP/1.1 418 I'm a teapot\r\nX-Pot: a\r\nx-pot: b\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

    async def scenario(transport):
        reply = await send(transport, wire.url + "/")
        assert isinstance(reply, AsyncHTTPResponse)
        assert (reply.status, reply.reason) == (418, "I'm a teapot")
        assert reply.headers["X-POT"] == "a, b"
        assert reply.headers.get_all("x-pot") == ["a", "b"]
        with pytest.raises(TypeError):
            reply.headers["X-Pot"] = "c"

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


@pytest.mark.parametrize("method, status", [("HEAD", 200), ("GET", 204), ("GET", 304)])
def test_a_reply_that_has_no_body_reads_as_empty(
    async_http_transport_kind, method, status
):
    def script(request):
        return (
            f"HTTP/1.1 {status} X\r\nContent-Length: 12\r\n\r\n".encode()
            if method == "HEAD"
            else f"HTTP/1.1 {status} X\r\n\r\n".encode()
        )

    async def scenario(transport):
        reply = await send(transport, wire.url + "/", method=method)
        assert await reply.read() == b""
        # The connection is free for the next request.
        again = await send(transport, wire.url + "/", method=method)
        assert await again.read() == b""

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


def test_a_chunked_body_and_a_body_without_a_length_are_read_whole(
    async_http_transport_kind,
):
    def script(request):
        if request.target == "/chunked":
            return (
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
            )
        return Reply(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nuntil the end", True)

    async def scenario(transport):
        first = await send(transport, wire.url + "/chunked")
        assert await first.read() == b"hello world"
        second = await send(transport, wire.url + "/open")
        assert await second.read() == b"until the end"

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


def test_a_streamed_body_is_read_in_pieces_of_the_size_asked(async_http_transport_kind):
    async def scenario(transport):
        async with await send(transport, wire.url + "/", stream=True) as reply:
            assert await reply.read(4) == b"0123"
            pieces = [piece async for piece in reply.iter_bytes(3)]
            assert pieces == [b"456", b"789"]
            assert await reply.read() == b""

    with Wire(lambda r: response(body=b"0123456789")) as wire:
        run(async_http_transport_kind, scenario)


def test_a_buffered_reply_can_be_read_after_it_is_closed(async_http_transport_kind):
    async def scenario(transport):
        reply = await send(transport, wire.url + "/")
        await reply.aclose()
        assert await reply.read() == b"kept"

    with Wire(lambda r: response(body=b"kept")) as wire:
        run(async_http_transport_kind, scenario)


def test_reading_a_streamed_reply_after_closing_it_is_an_error(
    async_http_transport_kind,
):
    async def scenario(transport):
        reply = await send(transport, wire.url + "/", stream=True)
        await reply.read(10)
        await reply.aclose()
        await reply.aclose()
        with pytest.raises(ValueError):
            await reply.read(10)

    with Wire(lambda r: response(body=b"x" * 100000)) as wire:
        run(async_http_transport_kind, scenario)


# --- content codings -----------------------------------------------------------


@pytest.mark.parametrize(
    "coding, compress",
    [
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("deflate", lambda data: zlib.compress(data)[2:-4]),
    ],
)
def test_a_coded_body_is_decoded_and_its_headers_stay_as_received(
    async_http_transport_kind, coding, compress
):
    packed = compress(b"decoded text " * 50)

    def script(request):
        return response(headers={"Content-Encoding": coding}, body=packed)

    async def scenario(transport):
        reply = await send(transport, wire.url + "/")
        assert await reply.read() == b"decoded text " * 50
        assert reply.headers["Content-Encoding"] == coding
        assert reply.headers["Content-Length"] == str(len(packed))

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


def test_a_body_is_not_decoded_when_the_request_chose_its_own_coding(
    async_http_transport_kind,
):
    packed = gzip.compress(b"raw")

    async def scenario(transport):
        headers = {"Accept-Encoding": "gzip"}
        reply = await send(transport, wire.url + "/", headers=headers)
        assert await reply.read() == packed

    with Wire(
        lambda r: response(headers={"Content-Encoding": "gzip"}, body=packed)
    ) as wire:
        run(async_http_transport_kind, scenario)


def test_corrupt_coded_data_is_an_io_error(async_http_transport_kind):
    async def scenario(transport):
        with pytest.raises(OSError) as excinfo:
            reply = await send(transport, wire.url + "/")
            await reply.read()
        assert excinfo.value.errno == errno.EIO

    with Wire(
        lambda r: response(headers={"Content-Encoding": "gzip"}, body=b"not gzip")
    ) as wire:
        run(async_http_transport_kind, scenario)


# --- replies that are not what they should be -----------------------------------


def test_a_body_shorter_than_its_length_is_an_io_error_not_a_short_read(
    async_http_transport_kind,
):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort", True)

    async def scenario(transport):
        with pytest.raises(OSError) as excinfo:
            reply = await send(transport, wire.url + "/")
            await reply.read()
        assert excinfo.value.errno == errno.EIO

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


def test_a_streamed_body_shorter_than_its_length_fails_when_it_runs_out(
    async_http_transport_kind,
):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nshort", True)

    async def scenario(transport):
        reply = await send(transport, wire.url + "/", stream=True)
        assert await reply.read(5) == b"short"
        with pytest.raises(OSError) as excinfo:
            await reply.read(1)
        assert excinfo.value.errno == errno.EIO
        await reply.aclose()

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


def test_a_connection_closed_without_a_reply_is_a_connection_error(
    async_http_transport_kind,
):
    async def scenario(transport):
        with pytest.raises(ConnectionError):
            await send(transport, wire.url + "/")

    with Wire(lambda r: Drop()) as wire:
        run(async_http_transport_kind, scenario)


def test_a_reply_that_is_not_http_is_an_io_error(async_http_transport_kind):
    async def scenario(transport):
        with pytest.raises(OSError) as excinfo:
            await send(transport, wire.url + "/")
        assert excinfo.value.errno == errno.EIO

    with Wire(lambda r: Reply(b"this is not http at all\r\n\r\n", True)) as wire:
        run(async_http_transport_kind, scenario)


def test_a_refused_connection_is_a_connection_error(async_http_transport_kind):
    async def scenario(transport):
        with pytest.raises(ConnectionError):
            await send(transport, f"http://127.0.0.1:{refusing_port()}/")

    run(async_http_transport_kind, scenario)


# --- refused before anything is sent -------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"X-A": "1\r\nX-Injected: yes"},
        {"X-A": "nul\x00byte"},
        {"X-A\r\nX-Injected": "yes"},
        {"X A": "space in the name"},
        {"": "empty name"},
    ],
)
def test_a_header_with_a_line_break_or_a_bad_name_is_refused_before_sending(
    async_http_transport_kind, headers
):
    async def scenario(transport):
        with pytest.raises(ValueError):
            await send(transport, wire.url + "/", headers=headers)

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
        assert wire.connections == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1/a\r\nX-Injected: yes",
        "ftp://127.0.0.1/x",
        "http://user:pw@127.0.0.1:1/x",
        "/relative",
    ],
)
def test_a_url_that_is_not_a_plain_http_url_is_refused_without_quoting_it(
    async_http_transport_kind, url
):
    async def scenario(transport):
        with pytest.raises(ValueError) as excinfo:
            await send(transport, url)
        assert "pw" not in str(excinfo.value) and "Injected" not in str(excinfo.value)

    run(async_http_transport_kind, scenario)


@pytest.mark.parametrize(
    "timeout", [0, -1, "5", (1, 2, 3), float("nan"), float("inf"), (5, float("nan"))]
)
def test_a_timeout_that_is_not_a_positive_number_is_refused(
    async_http_transport_kind, timeout
):
    async def scenario(transport):
        with pytest.raises((ValueError, TypeError)):
            await send(transport, wire.url + "/", timeout=timeout)

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
        assert wire.connections == 0


def test_a_body_that_is_text_is_refused(async_http_transport_kind):
    async def scenario(transport):
        with pytest.raises(TypeError):
            await send(transport, wire.url + "/", method="PUT", body="text")

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
        assert wire.connections == 0


# --- timeouts ----------------------------------------------------------------


def test_a_server_that_never_answers_is_a_timeout_after_the_read_timeout(
    async_http_transport_kind,
):
    async def scenario(transport):
        with pytest.raises(TimeoutError):
            await send(transport, wire.url + "/", timeout=(5, 0.3))

    with Wire(lambda r: Hang()) as wire:
        run(async_http_transport_kind, scenario)
        assert [(r.method, r.target) for r in wire.requests] == [("GET", "/")]


def test_a_body_that_stalls_is_a_timeout_when_it_is_read(async_http_transport_kind):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\npartial")

    async def scenario(transport):
        reply = await send(transport, wire.url + "/", stream=True, timeout=(5, 0.3))
        assert await reply.read(7) == b"partial"
        with pytest.raises(TimeoutError):
            await reply.read(1)
        await reply.aclose()

    with Wire(script) as wire:
        run(async_http_transport_kind, scenario)


# --- concurrency and the closer -------------------------------------------------


def test_requests_in_flight_together_are_served_together(async_http_transport_kind):
    """Each answer waits until three requests have arrived, so the three
    awaiting calls must have been sent at the same time."""
    gate = threading.Barrier(3, timeout=10)

    def script(request):
        gate.wait()
        return response(body=request.target.encode())

    async def scenario(transport):
        replies = await asyncio.gather(
            *(send(transport, f"{wire.url}/{n}") for n in range(3))
        )
        return [await reply.read() for reply in replies]

    with Wire(script) as wire:
        assert run(async_http_transport_kind, scenario) == [b"/0", b"/1", b"/2"]


def test_closing_a_transport_twice_is_harmless(async_http_transport_kind):
    async def scenario(transport):
        await (await send(transport, wire.url + "/")).read()
        await transport.aclose()
        await transport.aclose()

    with Wire() as wire:
        run(async_http_transport_kind, scenario)
