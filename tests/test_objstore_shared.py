"""What the object-store schemes share: how a transport failure reads, and a ranged read."""

import errno
import io

import pytest

from pathlib_next.uri.schemes import _objstore as store


def _named(name, *bases):
    return type(name, bases or (Exception,), {})


def test_a_timeout_is_found_by_the_name_of_its_type_or_of_what_it_wraps():
    read = _named("ReadTimeout")
    assert store.mentions_timeout(read("slow"))
    assert store.mentions_timeout(TimeoutError())
    assert store.mentions_timeout(_named("ConnectTimeoutError")("x"))
    outer = _named("ServiceRequestError")("sent")
    outer.inner_exception = read("slow")
    assert store.mentions_timeout(outer)
    reason = _named("MaxRetryError")("retries")
    reason.reason = read("slow")
    assert store.mentions_timeout(reason)
    wrapped = _named("RetryError")("deadline")
    wrapped.cause = _named("Timeout")("x")
    assert store.mentions_timeout(wrapped)


def test_a_refused_connection_is_not_a_timeout_although_urllib3_derives_it_from_one():
    connect_timeout = _named("ConnectTimeoutError")
    refused = _named("NewConnectionError", connect_timeout)("refused")
    assert not store.mentions_timeout(refused)
    wrapper = _named("ConnectionError")("Max retries")
    wrapper.args = (refused,)
    assert not store.mentions_timeout(wrapper)
    assert not store.mentions_timeout(ValueError("unrelated"))


@pytest.mark.parametrize(
    "kind, cls, number",
    [
        (store.TIMEOUT, TimeoutError, errno.ETIMEDOUT),
        (store.UNREACHABLE, ConnectionError, errno.EHOSTUNREACH),
        (store.INTERRUPTED, ConnectionResetError, errno.ECONNRESET),
        (store.FAILED, OSError, errno.EIO),
    ],
)
def test_a_transport_error_names_the_path_and_the_type_of_the_failure_only(
    kind, cls, number
):
    failure = _named("ReadTimeoutError")(
        "GET http://h/b/k?X-Amz-Signature=SECRET failed"
    )
    error = store.transport_error(kind, "S3", "s3://b/k", failure)
    assert type(error) is cls
    assert error.errno == number
    assert error.filename == "s3://b/k"
    assert "ReadTimeoutError" in str(error) and "S3" in str(error)
    assert "SECRET" not in str(error) and "http://h" not in str(error)


# --- RangeReader: an object read in ranged requests -------------------------


class _Object:
    """An object served by ranges, counting the requests."""

    def __init__(self, content):
        self.content = content
        self.requests = []

    def fetch(self, start, length):
        self.requests.append((start, length))
        return self.content[start : start + length]

    def size(self):
        self.requests.append("size")
        return len(self.content)

    def reader(self, chunk, preloaded=True):
        first = self.fetch(0, chunk) if preloaded else b""
        return store.RangeReader(self.fetch, self.size, first, chunk)


@pytest.mark.parametrize("length", [0, 1, 9, 10, 11, 29, 30, 31])
@pytest.mark.parametrize("step", [1, 3, 10, 64])
def test_a_ranged_read_returns_the_object_whatever_the_sizes(length, step):
    content = bytes(range(length))
    served = _Object(content)
    reader = io.BufferedReader(served.reader(10), step)
    assert reader.read() == content
    assert reader.read(5) == b""
    # One request per chunk, plus the one that finds the end.
    assert [request[0] for request in served.requests] == sorted(
        {0, *range(10, length + 1, 10)}
    )


def test_a_ranged_read_asks_for_a_chunk_only_when_it_is_needed():
    served = _Object(bytes(range(100)))
    reader = served.reader(10)
    assert reader.read(4) == bytes(range(4))
    assert reader.read(6) == bytes(range(4, 10))
    assert served.requests == [(0, 10)]
    assert reader.read(1) == bytes([10])
    assert served.requests == [(0, 10), (10, 10)]


def test_a_ranged_read_seeks_without_a_request_until_it_reads():
    served = _Object(bytes(range(100)))
    reader = served.reader(10)
    assert reader.seekable()
    assert reader.seek(55) == 55 and reader.tell() == 55
    assert served.requests == [(0, 10)]
    assert reader.read(3) == bytes([55, 56, 57])
    assert served.requests == [(0, 10), (55, 10)]
    assert reader.seek(-5, io.SEEK_CUR) == 53
    assert reader.read(2) == bytes([53, 54])
    assert reader.seek(2) == 2
    assert reader.read(2) == bytes([2, 3])
    assert served.requests[-1] == (2, 10)


def test_a_ranged_read_seeks_from_the_end_by_asking_the_size_once():
    served = _Object(bytes(range(100)))
    reader = served.reader(10)
    assert reader.seek(-4, io.SEEK_END) == 96
    assert reader.read() == bytes(range(96, 100))
    assert reader.seek(0, io.SEEK_END) == 100
    assert served.requests.count("size") == 1
    assert reader.read(1) == b""


def test_a_ranged_read_past_the_end_reads_nothing_and_asks_nothing_more():
    served = _Object(bytes(range(25)))
    reader = served.reader(10)
    assert reader.read() == bytes(range(25))
    asked = len(served.requests)
    reader.seek(40)
    assert reader.read(5) == b""
    reader.seek(25)
    assert reader.read(5) == b""
    assert len(served.requests) == asked


def test_a_ranged_read_refuses_a_bad_seek_and_a_closed_reader():
    reader = _Object(b"abc").reader(10)
    with pytest.raises(ValueError):
        reader.seek(-1)
    with pytest.raises(ValueError):
        reader.seek(0, 7)
    reader.close()
    with pytest.raises(ValueError):
        reader.read(1)


def test_a_failed_ranged_request_reaches_the_reader_as_it_was_raised():
    served = _Object(bytes(range(100)))
    reader = served.reader(10)

    def failing(start, length):
        raise ConnectionResetError("reset")

    reader._fetch = failing
    assert reader.read(10) == bytes(range(10))
    with pytest.raises(ConnectionResetError):
        reader.read(1)
