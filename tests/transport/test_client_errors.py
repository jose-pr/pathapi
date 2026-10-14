"""How a failed request becomes the `OSError` a path raises: the status of a
reply maps to the `pathlib` exception type for its meaning, and the error of a
transport is raised again without its cause, so that a URL, a header or a
credential it quotes is in no message or traceback."""

import errno
import traceback

import pytest

from pathlib_next.http import (
    HTTPClient,
    HTTPStatusError,
    os_error,
    raise_for_status,
    server_size,
    translate_errors,
)
from transport_fakes import FakeTransport, reply
from transport_wire import Wire, refusing_port, response

SUBJECT = "http://host.test/f"


def formatted(error: BaseException) -> str:
    """Everything a log line or traceback could show for `error`."""
    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    ) + "\n".join((str(error), repr(error)))


def failing_with(status, reason="", **options):
    client = HTTPClient(FakeTransport({("GET", SUBJECT): reply(status, reason=reason)}))
    with translate_errors(SUBJECT, **options):
        raise_for_status(client.request("GET", SUBJECT))


@pytest.mark.parametrize(
    "status, error, number",
    [
        (404, FileNotFoundError, errno.ENOENT),
        (410, FileNotFoundError, errno.ENOENT),
        (401, PermissionError, errno.EACCES),
        (403, PermissionError, errno.EACCES),
        (409, FileExistsError, errno.EEXIST),
    ],
)
def test_a_status_becomes_the_exception_pathlib_raises_for_its_meaning(
    status, error, number
):
    with pytest.raises(error) as excinfo:
        failing_with(status)
    assert type(excinfo.value) is error
    assert excinfo.value.errno == number
    assert excinfo.value.filename == SUBJECT


def test_a_conflict_means_a_missing_parent_for_a_write():
    with pytest.raises(FileNotFoundError):
        failing_with(409, conflict=FileNotFoundError)


@pytest.mark.parametrize("status", [405, 501])
def test_a_method_the_server_does_not_allow_is_a_permission_error_naming_the_status(
    status,
):
    with pytest.raises(PermissionError) as excinfo:
        failing_with(status)
    assert f"HTTP {status}" in str(excinfo.value)


@pytest.mark.parametrize("status", [400, 500, 502, 503, 429])
def test_any_other_status_is_oserror_eio_naming_it_and_the_subject(status):
    with pytest.raises(OSError) as excinfo:
        failing_with(status, "Reason")
    assert type(excinfo.value) is OSError
    assert excinfo.value.errno == errno.EIO
    assert f"HTTP Error {status} Reason for {SUBJECT}" in str(excinfo.value)


def test_a_status_error_keeps_the_reply_it_was_raised_for():
    answer = reply(404)
    with pytest.raises(HTTPStatusError) as excinfo:
        raise_for_status(answer)
    assert excinfo.value.response is answer
    raise_for_status(reply(399))


def test_an_error_is_raised_without_its_cause():
    with pytest.raises(FileNotFoundError) as excinfo:
        failing_with(404)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


@pytest.mark.parametrize(
    "make, expected",
    [
        (ConnectionError, ConnectionError),
        (ConnectionRefusedError, ConnectionError),
        (TimeoutError, TimeoutError),
    ],
)
def test_the_error_of_a_transport_never_reaches_the_raised_error(make, expected):
    # A transport's error can quote a proxy URL taken from the environment,
    # credentials included; none of it may be in the error or its traceback.
    quoted = "http://proxyuser:pr0xyS3cret@proxy.example:3128"
    transport = FakeTransport()
    transport.fail = make(f"ProxyError({quoted})")
    with pytest.raises(expected) as excinfo:
        with translate_errors(SUBJECT):
            HTTPClient(transport).request("GET", SUBJECT)
    assert "pr0xyS3cret" not in formatted(excinfo.value)
    assert SUBJECT in str(excinfo.value)
    assert type(make("")).__name__ in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_a_refused_connection_carries_no_cause(http_transport_kind):
    with HTTPClient() as client:
        with pytest.raises(ConnectionError) as excinfo:
            with translate_errors(SUBJECT):
                client.request("GET", f"http://127.0.0.1:{refusing_port()}/x")
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_a_stalled_server_is_a_timeout_error_without_a_cause(http_transport_kind):
    from transport_wire import Hang

    with Wire(lambda r: Hang()) as wire:
        with HTTPClient(timeout=0.5) as client:
            with pytest.raises(TimeoutError) as excinfo:
                with translate_errors(SUBJECT):
                    client.request("GET", wire.url + "/x")
    assert SUBJECT in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_an_error_that_is_not_a_request_failure_passes_through():
    with pytest.raises(KeyError):
        with translate_errors(SUBJECT):
            raise KeyError("x")


def test_os_error_is_built_as_pathlib_builds_it():
    error = os_error(IsADirectoryError, SUBJECT)
    assert (type(error), error.errno, error.filename) == (
        IsADirectoryError,
        errno.EISDIR,
        SUBJECT,
    )
    other = os_error(OSError, SUBJECT)
    assert (other.errno, other.filename) == (errno.EIO, SUBJECT)


@pytest.mark.parametrize(
    "text, size",
    [("5", 5), (" 12 ", 12), ("0", 0), (None, None), ("", None)]
    + [("-1", None), ("1e3", None), ("1.5", None), ("٣", None)]
    + [("9" * 20, None), ("0x10", None)],
)
def test_a_size_a_server_states_is_a_plain_count_or_nothing(text, size):
    assert server_size(text) == size


def test_a_reply_status_error_text_has_the_reason_escaped():
    esc = "\x1b[31m"
    with Wire(
        lambda r: (
            f"HTTP/1.1 500 Server {esc}Error\r\nContent-Length: 0\r\n\r\n"
        ).encode("latin-1")
    ) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                with translate_errors(SUBJECT):
                    raise_for_status(client.request("GET", wire.url + "/x"))
    assert "\x1b" not in str(excinfo.value)
    assert "Server \\x1b[31mError" in str(excinfo.value)
