"""What a transport puts on the wire and what it makes of the reply, against
loopback servers scripted at the byte level. The tests that hold for every
transport run once per transport; the built-in transport's connection
handling has its own tests in `test_http_pool.py`."""

import errno
import gzip
import io
import socket
import time
import tracemalloc
import zlib

import pytest

from http_wire import Drop, Hang, Reply, Wire, refusing_port, response
from waits import wait_until
from pathlib_next.uri.schemes.http import HTTPResponse, HTTPTransport

TIMEOUT = (5, 5)


def send(transport, url, method="GET", headers=None, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    return transport.send(method, url, headers=headers or {}, **kwargs)


# --- the request as the server receives it ------------------------------------


def test_request_line_host_and_headers_arrive_as_given(http_transport):
    with Wire() as wire:
        reply = send(
            http_transport, wire.url + "/a/b?x=1&y=%20", headers={"X-Probe": "1"}
        )
        assert reply.read() == b"ok"
    (request,) = wire.requests
    assert (request.method, request.target) == ("GET", "/a/b?x=1&y=%20")
    assert request.header("Host") == f"127.0.0.1:{wire.port}"
    assert request.header("X-Probe") == "1"


def test_an_empty_path_is_sent_as_a_slash_and_named_so_by_the_response(http_transport):
    with Wire() as wire:
        reply = send(http_transport, f"http://127.0.0.1:{wire.port}")
        reply.read()
    assert wire.requests[0].target == "/"
    assert reply.url == f"http://127.0.0.1:{wire.port}/"


def test_the_fragment_is_not_sent(http_transport):
    with Wire() as wire:
        reply = send(http_transport, wire.url + "/p#frag")
        reply.read()
    assert wire.requests[0].target == "/p"
    assert "#" not in reply.url


def test_a_path_that_is_not_ascii_is_percent_encoded(http_transport):
    with Wire() as wire:
        send(http_transport, wire.url + "/café x").read()
    assert wire.requests[0].target == "/caf%C3%A9%20x"


def test_gzip_and_deflate_are_advertised_unless_the_request_chooses(http_transport):
    with Wire() as wire:
        send(http_transport, wire.url + "/").read()
        send(
            http_transport, wire.url + "/", headers={"Accept-Encoding": "identity"}
        ).read()
    assert wire.requests[0].header("Accept-Encoding") == "gzip, deflate"
    assert wire.requests[1].header("Accept-Encoding") == "identity"


@pytest.mark.parametrize("method", ["PUT", "POST", "PATCH", "PROPFIND"])
def test_a_bytes_body_arrives_whole_with_its_length(http_transport, method):
    with Wire() as wire:
        send(http_transport, wire.url + "/f", method=method, body=b"payload").read()
    request = wire.requests[0]
    assert request.method == method
    assert request.body == b"payload"
    assert request.header("Content-Length") == "7"


def test_a_put_without_a_body_says_so(http_transport):
    with Wire() as wire:
        send(http_transport, wire.url + "/f", method="PUT").read()
    assert wire.requests[0].header("Content-Length") == "0"
    assert wire.requests[0].body == b""


def test_a_file_object_and_an_iterable_are_sent_whole(http_transport):
    with Wire() as wire:
        send(
            http_transport,
            wire.url + "/f",
            method="PUT",
            body=io.BytesIO(b"from a file"),
        ).read()
        send(
            http_transport,
            wire.url + "/g",
            method="PUT",
            body=(piece for piece in (b"in ", b"pieces")),
        ).read()
    assert [r.body for r in wire.requests] == [b"from a file", b"in pieces"]


def test_a_redirect_is_returned_and_not_followed(http_transport):
    def script(request):
        return response(302, {"Location": "/elsewhere"}, reason="Found")

    with Wire(script) as wire:
        reply = send(http_transport, wire.url + "/start")
        assert (reply.status, reply.headers["location"]) == (302, "/elsewhere")
        reply.close()
    assert [r.target for r in wire.requests] == ["/start"]


# --- the reply ------------------------------------------------------------------


def test_status_reason_and_case_blind_read_only_headers(http_transport):
    def script(request):
        return (
            b"HTTP/1.1 418 I'm a teapot\r\nX-Pot: a\r\nx-pot: b\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

    with Wire(script) as wire:
        reply = send(http_transport, wire.url + "/")
    assert (reply.status, reply.reason) == (418, "I'm a teapot")
    assert reply.headers["X-POT"] == "a, b"
    assert reply.headers.get_all("x-pot") == ["a", "b"]
    assert "x-Pot" in reply.headers and "missing" not in reply.headers
    with pytest.raises(TypeError):
        reply.headers["X-Pot"] = "c"


@pytest.mark.parametrize("method, status", [("HEAD", 200), ("GET", 204), ("GET", 304)])
def test_a_reply_that_has_no_body_reads_as_empty(http_transport, method, status):
    def script(request):
        return (
            f"HTTP/1.1 {status} X\r\nContent-Length: 12\r\n\r\n".encode()
            if method == "HEAD"
            else f"HTTP/1.1 {status} X\r\n\r\n".encode()
        )

    with Wire(script) as wire:
        reply = send(http_transport, wire.url + "/", method=method)
        assert reply.read() == b""
        # The connection is free for the next request.
        assert send(http_transport, wire.url + "/", method=method).read() == b""


def test_a_chunked_body_is_read_whole(http_transport):
    def script(request):
        return (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        )

    with Wire(script) as wire:
        assert send(http_transport, wire.url + "/").read() == b"hello world"


def test_a_body_without_a_length_ends_with_the_connection(http_transport):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nuntil the end", True)

    with Wire(script) as wire:
        assert send(http_transport, wire.url + "/").read() == b"until the end"


def test_a_streamed_body_is_read_in_pieces_of_the_size_asked(http_transport):
    with Wire(lambda r: response(body=b"0123456789")) as wire:
        with send(http_transport, wire.url + "/", stream=True) as reply:
            assert reply.read(4) == b"0123"
            assert list(reply.iter_bytes(3)) == [b"456", b"789"]
            assert reply.read() == b""


def test_a_buffered_reply_can_be_read_after_it_is_closed(http_transport):
    with Wire(lambda r: response(body=b"kept")) as wire:
        reply = send(http_transport, wire.url + "/")
        reply.close()
        assert reply.read() == b"kept"


def test_reading_a_streamed_reply_after_closing_it_is_an_error(http_transport):
    with Wire(lambda r: response(body=b"x" * 100000)) as wire:
        reply = send(http_transport, wire.url + "/", stream=True)
        reply.read(10)
        reply.close()
        reply.close()
        with pytest.raises(ValueError):
            reply.read(10)


# --- content codings -----------------------------------------------------------

PLAIN = b"the body that was compressed: " + bytes(range(256)) * 8


def _coded(raw, coding):
    return response(headers={"Content-Encoding": coding}, body=raw)


@pytest.mark.parametrize(
    "coding, compress",
    [
        ("gzip", gzip.compress),
        ("x-gzip", gzip.compress),
        ("deflate", zlib.compress),
        (
            "deflate",
            lambda data: zlib.compress(data)[2:-4],  # raw deflate, no zlib frame
        ),
    ],
    ids=["gzip", "x-gzip", "deflate-zlib", "deflate-raw"],
)
def test_a_coded_body_is_decoded(http_transport, coding, compress):
    with Wire(lambda r: _coded(compress(PLAIN), coding)) as wire:
        assert send(http_transport, wire.url + "/").read() == PLAIN


def test_concatenated_gzip_members_are_one_body(http_transport):
    body = gzip.compress(b"first ") + gzip.compress(b"second")
    with Wire(lambda r: _coded(body, "gzip")) as wire:
        assert send(http_transport, wire.url + "/").read() == b"first second"


def test_a_body_is_not_decoded_when_the_request_chose_its_own_coding(http_transport):
    packed = gzip.compress(PLAIN)
    with Wire(lambda r: _coded(packed, "gzip")) as wire:
        reply = send(
            http_transport, wire.url + "/", headers={"Accept-Encoding": "gzip"}
        )
        assert reply.read() == packed


def test_a_server_that_encodes_after_identity_was_asked_is_still_decoded(
    http_transport,
):
    with Wire(lambda r: _coded(gzip.compress(PLAIN), "gzip")) as wire:
        reply = send(
            http_transport, wire.url + "/", headers={"Accept-Encoding": "identity"}
        )
        assert reply.read() == PLAIN


def test_a_coding_that_was_not_offered_is_left_alone(http_transport):
    with Wire(lambda r: _coded(b"\x8b\x00\x00", "br")) as wire:
        assert send(http_transport, wire.url + "/").read() == b"\x8b\x00\x00"


def test_headers_stay_as_received_after_decoding(http_transport):
    packed = gzip.compress(PLAIN)
    with Wire(lambda r: _coded(packed, "gzip")) as wire:
        reply = send(http_transport, wire.url + "/")
        assert reply.headers["Content-Length"] == str(len(packed))
        assert reply.headers["Content-Encoding"] == "gzip"


def test_corrupt_coded_data_is_an_io_error(http_transport):
    with Wire(lambda r: _coded(b"not gzip at all, only text", "gzip")) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/").read()
    assert excinfo.value.errno == errno.EIO


def test_a_coded_body_that_ends_early_is_an_io_error(http_transport):
    packed = gzip.compress(PLAIN * 50)
    with Wire(lambda r: _coded(packed[:-30], "gzip")) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/").read()
    assert excinfo.value.errno == errno.EIO


def test_a_small_body_that_inflates_enormously_is_read_in_bounded_steps(http_transport):
    # 256 MiB of zeros packs into about 250 KiB. Reading ten bytes of it must
    # not inflate more than a step of it.
    packer = zlib.compressobj(wbits=16 + zlib.MAX_WBITS)
    packed = b"".join(
        [packer.compress(bytes(1 << 20)) for _ in range(256)] + [packer.flush()]
    )
    assert len(packed) < 1 << 20
    with Wire(lambda r: _coded(packed, "gzip")) as wire:
        reply = send(http_transport, wire.url + "/", stream=True)
        tracemalloc.start()
        try:
            assert reply.read(10) == bytes(10)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            reply.close()
    assert peak < 4 * 1024 * 1024


# --- a reply that ends early ---------------------------------------------------


def test_a_body_shorter_than_its_length_is_an_io_error_not_a_short_read(http_transport):
    def script(request):
        return Reply(
            b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 40, True
        )

    with Wire(script) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/")
    assert excinfo.value.errno == errno.EIO
    assert not isinstance(excinfo.value, (TimeoutError, ConnectionError))


def test_a_streamed_body_shorter_than_its_length_fails_when_it_runs_out(http_transport):
    def script(request):
        return Reply(
            b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 40, True
        )

    with Wire(script) as wire:
        with send(http_transport, wire.url + "/", stream=True) as reply:
            assert reply.read(10) == b"x" * 10
            with pytest.raises(OSError) as excinfo:
                reply.read()
    assert excinfo.value.errno == errno.EIO


def test_a_chunked_body_that_stops_mid_chunk_is_an_io_error(http_transport):
    def script(request):
        return Reply(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\nA\r\nonly",
            True,
        )

    with Wire(script) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/")
    assert excinfo.value.errno == errno.EIO


def test_a_connection_closed_without_a_reply_is_a_connection_error(http_transport):
    with Wire(lambda r: Drop()) as wire:
        with pytest.raises(ConnectionError):
            send(http_transport, wire.url + "/")


def test_a_reply_that_is_not_http_is_an_io_error(http_transport):
    with Wire(lambda r: Reply(b"this is not http at all\r\n\r\n", True)) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/")
    assert excinfo.value.errno == errno.EIO


def test_a_reply_with_more_headers_than_a_client_reads_is_an_io_error(http_transport):
    headers = "".join(f"X-{i}: v\r\n" for i in range(500))
    raw = f"HTTP/1.1 200 OK\r\n{headers}Content-Length: 0\r\n\r\n".encode()
    with Wire(lambda r: Reply(raw, True)) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/")
    assert excinfo.value.errno == errno.EIO


def test_a_status_line_of_endless_length_is_an_io_error(http_transport):
    raw = b"HTTP/1.1 200 " + b"x" * 200000 + b"\r\nContent-Length: 0\r\n\r\n"
    with Wire(lambda r: Reply(raw, True)) as wire:
        with pytest.raises(OSError) as excinfo:
            send(http_transport, wire.url + "/")
    assert excinfo.value.errno == errno.EIO


def test_a_refused_connection_is_a_connection_error(http_transport):
    with pytest.raises(ConnectionError):
        send(http_transport, f"http://127.0.0.1:{refusing_port()}/")


# --- refused before anything is sent -------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"X-A": "1\r\nX-Injected: yes"},
        {"X-A": "1\nX-Injected: yes"},
        {"X-A": "1\rX-Injected: yes"},
        {"X-A": "nul\x00byte"},
        {"X-A\r\nX-Injected": "yes"},
        {"X A": "space in the name"},
        {"": "empty name"},
    ],
)
def test_a_header_with_a_line_break_or_a_bad_name_is_refused_before_sending(
    http_transport, headers
):
    with Wire() as wire:
        with pytest.raises(ValueError):
            send(http_transport, wire.url + "/", headers=headers)
        assert wire.connections == 0 and wire.requests == []


def test_the_refusal_of_a_header_does_not_quote_its_value(http_transport):
    secret = "Bearer TOPSECRET-1234\r\nX-Injected: yes"
    with Wire() as wire:
        with pytest.raises(ValueError) as excinfo:
            send(http_transport, wire.url + "/", headers={"Authorization": secret})
    assert "TOPSECRET" not in str(excinfo.value)
    assert "TOPSECRET" not in repr(excinfo.value)


@pytest.mark.parametrize(
    "path",
    ["/a\r\nX-Injected: yes", "/a\nb", "/a\rb", "/a\x00b", "/a\tb", "/a\x7fb"],
)
def test_a_url_with_a_control_character_is_refused_before_sending(http_transport, path):
    with Wire() as wire:
        with pytest.raises(ValueError):
            send(http_transport, wire.url + path)
        assert wire.connections == 0


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1/x",
        "file:///etc/hosts",
        "/relative/path",
        "http:///no-host",
        "http://[::1/x",
    ],
)
def test_a_url_that_is_not_an_http_url_is_refused(http_transport, url):
    with pytest.raises(ValueError):
        send(http_transport, url)


def test_credentials_in_a_url_are_refused_and_not_quoted(http_transport):
    with Wire() as wire:
        with pytest.raises(ValueError) as excinfo:
            send(http_transport, f"http://alice:S3CR3T@127.0.0.1:{wire.port}/x")
        assert wire.connections == 0
    assert "S3CR3T" not in str(excinfo.value)


@pytest.mark.parametrize("timeout", [0, -1, "5", (1, 2, 3), True, (5, -1)])
def test_a_timeout_that_is_not_a_positive_number_is_refused(http_transport, timeout):
    with Wire() as wire:
        with pytest.raises((ValueError, TypeError)):
            send(http_transport, wire.url + "/", timeout=timeout)


def test_a_body_that_is_text_is_refused(http_transport):
    with Wire() as wire:
        with pytest.raises(TypeError):
            send(http_transport, wire.url + "/", method="PUT", body="text")
        assert wire.connections == 0


# --- timeouts ----------------------------------------------------------------


def test_a_server_that_never_answers_is_a_timeout_after_the_read_timeout(
    http_transport,
):
    with Wire(lambda r: Hang()) as wire:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            send(http_transport, wire.url + "/", timeout=(5, 0.3))
    assert time.monotonic() - started < 4


def test_a_body_that_stalls_is_a_timeout_when_it_is_read(http_transport):
    def script(request):
        return Reply(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\npartial")

    with Wire(script) as wire:
        reply = send(http_transport, wire.url + "/", stream=True, timeout=(5, 0.3))
        assert reply.read(7) == b"partial"
        with pytest.raises(TimeoutError):
            reply.read(1)
        reply.close()


def test_a_single_number_is_both_halves_of_the_timeout(http_transport):
    with Wire(lambda r: Hang()) as wire:
        with pytest.raises(TimeoutError):
            send(http_transport, wire.url + "/", timeout=0.3)


# --- the built-in transport's two timeouts ---------------------------------


def test_the_connect_half_bounds_the_connection_and_the_read_half_the_waits(
    monkeypatch,
):
    from pathlib_next.uri.schemes._http import _builtin

    seen = {}

    class Recorded:
        """A connected socket that notes the timeout it is given."""

        def __init__(self, sock):
            self._sock = sock

        def settimeout(self, value):
            seen["read"] = value
            self._sock.settimeout(value)

        def __getattr__(self, name):
            return getattr(self._sock, name)

    real = socket.create_connection

    def create_connection(address, timeout=None, *args, **kwargs):
        seen["connect"] = timeout
        return Recorded(real(address, timeout, *args, **kwargs))

    monkeypatch.setattr(_builtin._socket, "create_connection", create_connection)
    with Wire() as wire, HTTPTransport() as transport:
        send(transport, wire.url + "/", timeout=(3.5, 7.5)).read()
    assert seen == {"connect": 3.5, "read": 7.5}


# --- credentials never in a repr or a message --------------------------------


def test_a_transport_repr_shows_no_proxy_credentials():
    proxies = {"http": "http://user:PROXYSECRET@proxy.example:3128"}
    transport = HTTPTransport(proxies=proxies)
    assert "PROXYSECRET" not in repr(transport)
    assert "PROXYSECRET" not in str(transport)
    transport.close()


def test_an_http_response_repr_shows_no_header_values(http_transport):
    with Wire(
        lambda r: response(headers={"Set-Cookie": "session=SECRETCOOKIE"})
    ) as wire:
        reply = send(http_transport, wire.url + "/")
    assert "SECRETCOOKIE" not in repr(reply) + repr(reply.headers)


# --- the response object on its own ------------------------------------------


def test_a_response_can_be_built_from_bytes_for_a_fake_transport():
    reply = HTTPResponse(
        200, "OK", {"Content-Type": "text/plain"}, "http://h/x", b"abc"
    )
    assert (reply.status, reply.reason, reply.url) == (200, "OK", "http://h/x")
    assert reply.headers["content-type"] == "text/plain"
    assert reply.read(2) == b"ab" and reply.read() == b"c" and reply.read() == b""


def test_a_response_decodes_only_what_its_headers_say():
    packed = gzip.compress(b"plain text")
    assert HTTPResponse(200, "", {"Content-Encoding": "gzip"}, "u", packed).read() == (
        b"plain text"
    )
    assert HTTPResponse(200, "", {}, "u", packed).read() == packed
    assert (
        HTTPResponse(
            200, "", {"Content-Encoding": "gzip"}, "u", packed, decode=False
        ).read()
        == packed
    )


# --- interim (1xx) replies ---------------------------------------------------------

INTERIM = b"HTTP/1.1 103 Early Hints\r\nLink: </style.css>; rel=preload\r\n\r\n"


def test_an_interim_reply_is_never_the_previous_answer_of_the_next_request(
    http_transport,
):
    def script(request):
        if request.target == "/a":
            return Reply(INTERIM, later=response(body=b"A"), delay=0.3)
        return response(body=b"B")

    with Wire(script) as wire:
        try:
            first = send(http_transport, wire.url + "/a").read()
        except OSError:
            first = None  # a transport that cannot skip it says so
        assert send(http_transport, wire.url + "/b").read() == b"B"
    assert first in (None, b"A")


def test_the_built_in_transport_skips_interim_replies_and_returns_the_final_one():
    def script(request):
        return Reply(INTERIM * 2, later=response(body=b"FINAL"), delay=0.2)

    with Wire(script) as wire, HTTPTransport() as transport:
        reply = send(transport, wire.url + "/")
        assert (reply.status, reply.read()) == (200, b"FINAL")
        # The connection is clean for the next request.
        assert send(transport, wire.url + "/").read() == b"FINAL"
    assert wire.connections == 1


def test_a_bounded_number_of_interim_replies_is_skipped():
    for count, ok in ((8, True), (9, False)):

        def script(request, count=count):
            return Reply(
                b"HTTP/1.1 100 Continue\r\n\r\n" * count + response(body=b"done")
            )

        with Wire(script) as wire, HTTPTransport() as transport:
            if ok:
                assert send(transport, wire.url + "/").read() == b"done"
            else:
                with pytest.raises(OSError) as excinfo:
                    send(transport, wire.url + "/")
                assert excinfo.value.errno == errno.EIO
                # The connection was given up, not kept with the rest unread.
                assert wait_until(lambda: wire.closed == [0])


def test_an_endless_stream_of_100_continue_is_given_up_not_waited_out():
    endless = b"HTTP/1.1 100 Continue\r\n\r\n" * 5000

    with Wire(lambda request: Reply(endless)) as wire, HTTPTransport() as transport:
        with pytest.raises(OSError) as excinfo:
            send(transport, wire.url + "/", timeout=(2, 2))
    # Given up for sending too many, not for the server going quiet.
    assert excinfo.value.errno == errno.EIO
    assert not isinstance(excinfo.value, TimeoutError)


def test_a_status_below_200_is_never_the_final_reply():
    with Wire(lambda request: Reply(INTERIM, close=True)) as wire:
        with HTTPTransport() as transport:
            with pytest.raises(OSError):
                send(transport, wire.url + "/")
