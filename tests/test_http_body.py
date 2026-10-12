"""How a request body is framed, whichever transport sends it. A body whose
length is known without reading it goes under a `Content-Length`; one whose
length is not goes chunked; a `Content-Length` the caller set is trusted; and
a body never sends more or fewer bytes than the length it was sent under. Each
test reads what the loopback server received."""

import io

import pytest

from http_wire import Drop, Wire, response
from pathlib_next.uri.schemes._http import _body
from pathlib_next.uri.schemes.http import HTTPTransport

TIMEOUT = (5, 5)
PAYLOAD = b"0123456789abcdef"


def put(transport, wire, body, headers=None, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    return transport.send(
        "PUT", wire.url + "/up", headers=headers or {}, body=body, **kwargs
    ).read()


def framing(request):
    """`(Content-Length, Transfer-Encoding)` as the server received them."""
    return request.header("Content-Length"), request.header("Transfer-Encoding")


class Unseekable(io.RawIOBase):
    """A stream that reads but cannot seek or report a size, like a pipe."""

    def __init__(self, data):
        self._data = io.BytesIO(data)

    def readable(self):
        return True

    def seekable(self):
        return False

    def readinto(self, buffer):
        chunk = self._data.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)


class Growing(io.BytesIO):
    """A file that gains bytes after it was measured."""

    grown = False

    def read(self, size=-1):
        if not self.grown:
            self.grown = True
            here = self.tell()
            self.seek(0, 2)
            self.write(b"APPENDED-AFTER-THE-LENGTH-WAS-TAKEN")
            self.seek(here)
        return super().read(size)


class Shrinking(io.BytesIO):
    """A file that ends sooner than it said it would."""

    def read(self, size=-1):
        return super().read(min(size, 4) if size and size > 0 else 4)

    def seek(self, offset, whence=0):
        position = super().seek(offset, whence)
        if whence == 2:
            return position + 100  # claims more than it holds
        return position


# --- a length that is known: Content-Length ------------------------------------------


def test_bytes_go_under_their_length(http_transport):
    with Wire() as wire:
        put(http_transport, wire, PAYLOAD)
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD)), None)
    assert request.body == PAYLOAD


def test_a_list_of_bytes_goes_under_the_sum_of_its_pieces(http_transport):
    with Wire() as wire:
        put(http_transport, wire, [b"0123", b"4567", b"89"])
    (request,) = wire.requests
    assert framing(request) == ("10", None)
    assert request.body == b"0123456789"


def test_a_bytes_io_at_the_start_goes_under_its_length(http_transport):
    with Wire() as wire:
        put(http_transport, wire, io.BytesIO(PAYLOAD))
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD)), None)
    assert request.body == PAYLOAD


def test_a_bytes_io_part_way_sends_what_is_left_under_that_length(http_transport):
    source = io.BytesIO(PAYLOAD)
    source.seek(6)
    with Wire() as wire:
        put(http_transport, wire, source)
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD) - 6), None)
    assert request.body == PAYLOAD[6:]


def test_an_open_file_goes_under_its_length(http_transport, tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(PAYLOAD)
    with Wire() as wire, open(path, "rb") as source:
        put(http_transport, wire, source)
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD)), None)
    assert request.body == PAYLOAD


def test_an_open_file_part_way_sends_what_is_left_under_that_length(
    http_transport, tmp_path
):
    path = tmp_path / "upload.bin"
    path.write_bytes(PAYLOAD)
    with Wire() as wire, open(path, "rb") as source:
        source.seek(10)
        put(http_transport, wire, source)
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD) - 10), None)
    assert request.body == PAYLOAD[10:]


def test_a_file_that_cannot_seek_but_has_a_size_goes_under_it(http_transport, tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(PAYLOAD)

    class NoSeek(io.BufferedReader):
        def seekable(self):
            return False

    with Wire() as wire, NoSeek(io.FileIO(path)) as source:
        source.read(4)
        put(http_transport, wire, source)
    (request,) = wire.requests
    assert framing(request) == (str(len(PAYLOAD) - 4), None)
    assert request.body == PAYLOAD[4:]


def test_an_empty_file_goes_under_a_length_of_zero(http_transport):
    with Wire() as wire:
        put(http_transport, wire, io.BytesIO(b""))
    (request,) = wire.requests
    assert framing(request) == ("0", None)
    assert request.body == b""


def test_the_position_of_a_file_is_restored_after_it_is_measured():
    source = io.BytesIO(PAYLOAD)
    source.seek(5)
    assert _body.body_length(source) == len(PAYLOAD) - 5
    assert source.tell() == 5


# --- a length that is not known: chunked ---------------------------------------------


def test_a_stream_that_cannot_seek_is_sent_chunked(http_transport):
    with Wire() as wire:
        put(http_transport, wire, Unseekable(PAYLOAD))
    (request,) = wire.requests
    assert framing(request) == (None, "chunked")
    assert request.body == PAYLOAD


def test_a_generator_is_sent_chunked(http_transport):
    with Wire() as wire:
        put(http_transport, wire, (piece for piece in (b"0123", b"4567", b"89")))
    (request,) = wire.requests
    assert framing(request) == (None, "chunked")
    assert request.body == b"0123456789"


# --- a length the caller set ---------------------------------------------------------


def test_a_length_the_caller_set_is_used_and_nothing_is_chunked(http_transport):
    with Wire() as wire:
        put(
            http_transport,
            wire,
            (piece for piece in (b"0123", b"4567", b"89")),
            headers={"Content-Length": "10"},
        )
    (request,) = wire.requests
    assert framing(request) == ("10", None)
    assert request.body == b"0123456789"


def test_a_length_the_caller_set_cuts_a_longer_body_at_that_length(http_transport):
    with Wire() as wire:
        put(
            http_transport,
            wire,
            io.BytesIO(PAYLOAD),
            headers={"Content-Length": "6"},
        )
        put(http_transport, wire, b"next")
    first, second = wire.requests
    assert framing(first) == ("6", None)
    assert first.body == PAYLOAD[:6]
    # Nothing of the rest was sent: the next request on the connection is whole.
    assert (second.method, second.target, second.body) == ("PUT", "/up", b"next")


def test_a_length_the_caller_set_on_bytes_cuts_them_there(http_transport):
    with Wire() as wire:
        put(http_transport, wire, PAYLOAD, headers={"content-length": "4"})
    (request,) = wire.requests
    assert framing(request) == ("4", None)
    assert request.body == PAYLOAD[:4]


@pytest.mark.parametrize(
    "body", [PAYLOAD, io.BytesIO(PAYLOAD), [PAYLOAD]], ids=["bytes", "file", "list"]
)
def test_a_length_larger_than_a_body_whose_size_is_known_is_refused(
    http_transport, body
):
    with Wire() as wire:
        with pytest.raises(ValueError):
            put(http_transport, wire, body, headers={"Content-Length": "99"})
        assert wire.connections == 0


@pytest.mark.parametrize("value", ["-1", "ten", "1.5", "", "1, 2"])
def test_a_length_that_is_not_a_count_of_bytes_is_refused(http_transport, value):
    with Wire() as wire:
        with pytest.raises(ValueError):
            put(http_transport, wire, PAYLOAD, headers={"Content-Length": value})
        assert wire.connections == 0


def test_a_generator_that_ends_before_the_length_the_caller_set_is_an_error(
    http_transport,
):
    with Wire() as wire:
        with pytest.raises(OSError) as excinfo:
            put(
                http_transport,
                wire,
                (piece for piece in (b"0123", b"4567")),
                headers={"Content-Length": "10"},
                timeout=(5, 0.5),
            )
    # Not a wait for the rest of the body that never comes.
    assert not isinstance(excinfo.value, TimeoutError)


# --- a length that was right when it was taken ---------------------------------------


def test_a_file_that_grew_after_it_was_measured_sends_only_the_length_it_stated(
    http_transport,
):
    with Wire() as wire:
        put(http_transport, wire, Growing(PAYLOAD))
        put(http_transport, wire, b"next")
    first, second = wire.requests
    assert framing(first) == (str(len(PAYLOAD)), None)
    assert first.body == PAYLOAD
    # Nothing past the length was sent: the next request is not preceded by it.
    assert (second.method, second.target, second.body) == ("PUT", "/up", b"next")


def test_a_file_that_ends_before_the_length_it_stated_is_an_error(http_transport):
    with Wire() as wire:
        with pytest.raises(OSError) as excinfo:
            put(http_transport, wire, Shrinking(PAYLOAD), timeout=(5, 0.5))
    assert not isinstance(excinfo.value, TimeoutError)


# --- a body with a length is still sent once -----------------------------------------


def test_a_file_with_a_known_length_is_not_sent_again_on_a_dropped_connection():
    def script(request):
        if request.connection == 0 and request.body == b"two":
            return Drop()
        return response(body=b"ok")

    with Wire(script) as wire:
        with HTTPTransport() as transport:
            put(transport, wire, io.BytesIO(b"one"))
            with pytest.raises(OSError):
                put(transport, wire, io.BytesIO(b"two"))
    assert [(r.connection, r.body) for r in wire.requests] == [
        (0, b"one"),
        (0, b"two"),
    ]
