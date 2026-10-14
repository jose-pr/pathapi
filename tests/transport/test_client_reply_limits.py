"""How much of a reply the library reads. A reply read whole for its status or
for a small document is held to `MAX_REPLY_BYTES`, counted in DECODED bytes,
whichever transport sent it; one nobody looks at is read up to that limit and
dropped; and a connection whose reply was longer is not kept."""

import errno
import gzip

import pytest

import pathlib_next.http as http
from pathlib_next.http import (
    HTTPClient,
    drop_body,
    json_body,
    read_limited,
)
from transport_wire import Wire, response, wait_until

pytestmark = pytest.mark.usefixtures("http_transport_kind")

CAP = 64 * 1024
INFLATED = bytes(1024 * 1024)  # 16 times the cap, a kilobyte on the wire
BOMB = gzip.compress(INFLATED)
CODED = {"Content-Encoding": "gzip"}


@pytest.fixture(autouse=True)
def small_cap(monkeypatch):
    monkeypatch.setattr(http, "MAX_REPLY_BYTES", CAP)


def long_reply(status=200, extra=None):
    """A reply whose body is three times the limit on the wire as well."""
    return response(status, extra or {}, b"x" * (3 * CAP))


def bomb(status=200, extra=None):
    return response(status, {**CODED, **(extra or {})}, BOMB)


def test_the_setting_is_read_where_it_takes_effect():
    from pathlib_next.http import _settings

    assert _settings.MAX_REPLY_BYTES == CAP == http.MAX_REPLY_BYTES


def test_the_default_limit_is_eight_mebibytes(monkeypatch):
    monkeypatch.undo()
    assert http.MAX_REPLY_BYTES == 8 * 1024 * 1024


# --- replies nobody looks at ---------------------------------------------------------


def test_a_dropped_reply_over_the_limit_does_not_keep_its_connection():
    with Wire(lambda r: long_reply(201)) as wire:
        with HTTPClient() as client:
            drop_body(
                client.request("PUT", wire.url + "/file", data=b"data", stream=True)
            )
            assert wait_until(lambda: wire.closed == [0])
            drop_body(
                client.request("PUT", wire.url + "/file", data=b"again", stream=True)
            )
    assert wire.connections == 2
    assert [r.body for r in wire.requests] == [b"data", b"again"]


def test_a_dropped_small_reply_is_read_so_the_connection_serves_the_next_one():
    with Wire(lambda r: response(201, {}, b"x" * 1000)) as wire:
        with HTTPClient() as client:
            drop_body(
                client.request("PUT", wire.url + "/file", data=b"one", stream=True)
            )
            drop_body(
                client.request("PUT", wire.url + "/file", data=b"two", stream=True)
            )
    assert wire.connections == 1


def test_a_coded_reply_is_dropped_without_inflating_it_whole():
    with Wire(lambda r: bomb(201)) as wire:
        with HTTPClient() as client:
            for body in (b"data", b"again"):
                reply = client.request(
                    "PUT", wire.url + "/file", data=body, stream=True
                )
                drop_body(reply)
    assert [r.body for r in wire.requests] == [b"data", b"again"]


def test_the_limit_of_a_dropped_reply_can_be_given_in_the_call():
    with Wire(lambda r: long_reply(200)) as wire:
        with HTTPClient() as client:
            for _ in range(2):
                reply = client.request("GET", wire.url + "/file", stream=True)
                drop_body(reply, 10 * CAP)
    assert wire.connections == 1


# --- replies read for what they say ----------------------------------------------------


def test_a_reply_over_the_limit_is_efbig_counted_in_decoded_bytes():
    with Wire(lambda r: bomb(207)) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/dir/", stream=True)
            with pytest.raises(OSError) as excinfo:
                read_limited(reply, "the directory")
        # A kilobyte on the wire, so a check of the stated length would pass.
        assert len(BOMB) < CAP
    assert excinfo.value.errno == errno.EFBIG
    assert "the directory" in str(excinfo.value)
    assert str(CAP) in str(excinfo.value)
    assert "MAX_REPLY_BYTES" in str(excinfo.value)


def test_a_plain_reply_that_is_too_long_is_efbig_as_well_and_not_waited_for():
    with Wire(lambda r: long_reply(207)) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/dir/", stream=True)
            with pytest.raises(OSError) as excinfo:
                read_limited(reply, "the directory")
            # Past the limit, the rest of the body is not waited for.
            assert wait_until(lambda: wire.closed == [0])
    assert excinfo.value.errno == errno.EFBIG


def test_a_reply_within_the_limit_is_read_and_the_connection_is_kept():
    with Wire(lambda r: response(207, {}, b"<multistatus/>")) as wire:
        with HTTPClient() as client:
            for _ in range(2):
                reply = client.request("GET", wire.url + "/dir/", stream=True)
                assert read_limited(reply, "the directory") == b"<multistatus/>"
    assert wire.connections == 1


def test_the_limit_is_read_each_time_so_raising_it_lets_a_larger_reply_through(
    monkeypatch,
):
    with Wire(lambda r: response(207, {}, b" " * (2 * CAP))) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError):
                read_limited(client.request("GET", wire.url + "/x", stream=True), "x")
            monkeypatch.setattr(http, "MAX_REPLY_BYTES", 4 * CAP)
            reply = client.request("GET", wire.url + "/x", stream=True)
            assert len(read_limited(reply, "x")) == 2 * CAP


def test_the_limit_of_a_read_can_be_given_in_the_call():
    with Wire(lambda r: response(200, {}, b"x" * (2 * CAP))) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/x", stream=True)
            assert len(read_limited(reply, "x", 4 * CAP)) == 2 * CAP


def test_a_json_reply_over_the_limit_is_efbig():
    with Wire(lambda r: bomb(200, {"Content-Type": "application/json"})) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/api", stream=True)
            with pytest.raises(OSError) as excinfo:
                json_body(reply, "the API")
    assert excinfo.value.errno == errno.EFBIG


def test_a_json_reply_within_the_limit_is_the_document():
    with Wire(lambda r: response(200, {}, b'{"name": "n", "size": [1, 2]}')) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/api", stream=True)
            assert json_body(reply, "the API") == {"name": "n", "size": [1, 2]}


def test_a_reply_that_is_not_json_is_a_value_error():
    with Wire(lambda r: response(200, {}, b"<html>")) as wire:
        with HTTPClient() as client:
            reply = client.request("GET", wire.url + "/api", stream=True)
            with pytest.raises(ValueError):
                json_body(reply, "the API")


# --- the client's own reads --------------------------------------------------------------


def test_the_client_reads_a_reply_whole_only_up_to_the_limit(http_transport):
    with Wire(lambda r: bomb(200)) as wire:
        client = HTTPClient(http_transport)
        with pytest.raises(OSError) as excinfo:
            client.request("GET", wire.url + "/x")
        assert excinfo.value.errno == errno.EFBIG
        assert wire.url in str(excinfo.value)
        # A caller that reads the body itself is not limited.
        with client.request("GET", wire.url + "/x", stream=True) as streamed:
            assert len(streamed.read()) == len(INFLATED)


def test_a_reply_within_the_limit_is_returned_read_and_closed(http_transport):
    with Wire(lambda r: response(200, {}, b"abc")) as wire:
        reply = HTTPClient(http_transport).request("GET", wire.url + "/x")
        assert reply.read() == b"abc"
        assert reply.read() == b""
