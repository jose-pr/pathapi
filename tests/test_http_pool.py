"""How the built-in transport reuses connections: one idle connection per
thread and server, a resend only for a request that can be sent twice, and
every connection closed when its thread ends or the transport is closed."""

import gc
import io
import threading

import pytest

from http_wire import Drop, Reply, Wire, response
from pathlib_next.uri.schemes._http import _builtin
from pathlib_next.uri.schemes.http import HTTPTransport
from waits import wait_until

TIMEOUT = (5, 5)


def get(transport, url, **kwargs):
    return transport.send("GET", url, headers={}, timeout=TIMEOUT, **kwargs)


def send(transport, method, url, **kwargs):
    return transport.send(method, url, headers={}, timeout=TIMEOUT, **kwargs)


@pytest.fixture
def transport():
    with HTTPTransport() as transport:
        yield transport


@pytest.fixture
def unnoticed(monkeypatch):
    """The client does not see an idle connection die before it sends on it: the
    server closes in the instant between the check and the request."""
    monkeypatch.setattr(_builtin, "_unusable", lambda sock: False)


def closing_after_each_reply(request):
    """Answers, then closes the connection without having said so."""
    return Reply(response(body=b"ok"), close=True)


# --- reuse ------------------------------------------------------------------


def test_requests_in_one_thread_to_one_server_share_a_connection(transport):
    with Wire() as wire:
        for _ in range(3):
            assert get(transport, wire.url + "/").read() == b"ok"
    assert wire.connections == 1
    assert [r.connection for r in wire.requests] == [0, 0, 0]


def test_a_reply_never_read_to_its_end_does_not_hold_its_connection(transport):
    with Wire(lambda r: response(body=b"x" * 200000)) as wire:
        first = get(transport, wire.url + "/", stream=True)
        first.read(10)
        first.close()
        get(transport, wire.url + "/").read()
    assert wire.connections == 2


def test_a_reply_read_to_its_end_gives_its_connection_back(transport):
    with Wire(lambda r: response(body=b"x" * 200000)) as wire:
        with get(transport, wire.url + "/", stream=True) as first:
            assert len(first.read()) == 200000
        get(transport, wire.url + "/").read()
    assert wire.connections == 1


def test_two_replies_open_at_once_use_two_connections(transport):
    with Wire(lambda r: response(body=b"x" * 200000)) as wire:
        a = get(transport, wire.url + "/", stream=True)
        b = get(transport, wire.url + "/", stream=True)
        assert len(a.read()) == len(b.read()) == 200000
    assert wire.connections == 2


def test_a_server_that_says_close_is_not_reused(transport):
    def script(request):
        return response(headers={"Connection": "close"}, body=b"ok")

    with Wire(script) as wire:
        get(transport, wire.url + "/").read()
        get(transport, wire.url + "/").read()
    assert wire.connections == 2


def test_different_servers_get_different_connections(transport):
    with Wire() as one, Wire() as two:
        get(transport, one.url + "/").read()
        get(transport, two.url + "/").read()
        get(transport, one.url + "/").read()
    assert (one.connections, two.connections) == (1, 1)


# --- a connection the server dropped -------------------------------------------


@pytest.mark.parametrize(
    "method", ["GET", "HEAD", "OPTIONS", "PUT", "DELETE", "PROPFIND"]
)
def test_a_repeatable_request_is_sent_again_on_a_new_connection(
    transport, unnoticed, method
):
    with Wire(closing_after_each_reply) as wire:
        send(
            transport,
            method,
            wire.url + "/f",
            body=b"data" if method == "PUT" else None,
        ).read()
        wait_until(lambda: 0 in wire.closed)
        reply = send(
            transport,
            method,
            wire.url + "/f",
            body=b"data" if method == "PUT" else None,
        )
        assert reply.status == 200
    assert wire.connections == 2
    assert [r.method for r in wire.requests] == [method, method]


@pytest.mark.parametrize("method", ["POST", "PATCH", "MKCOL", "MOVE"])
def test_a_request_that_cannot_be_repeated_is_not_sent_again(
    transport, unnoticed, method
):
    with Wire(closing_after_each_reply) as wire:
        send(transport, method, wire.url + "/f", body=b"data").read()
        wait_until(lambda: 0 in wire.closed)
        with pytest.raises(ConnectionError):
            send(transport, method, wire.url + "/f", body=b"data")
    # The second request was never delivered, and no new connection was made
    # to deliver it a second time.
    assert wire.connections == 1
    assert len(wire.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        lambda: io.BytesIO(b"from a file"),
        lambda: (piece for piece in (b"in ", b"pieces")),
    ],
    ids=["file", "iterator"],
)
def test_a_body_that_cannot_be_sent_twice_is_not_sent_again(transport, unnoticed, body):
    with Wire(closing_after_each_reply) as wire:
        send(transport, "PUT", wire.url + "/f", body=b"first").read()
        wait_until(lambda: 0 in wire.closed)
        with pytest.raises(ConnectionError):
            send(transport, "PUT", wire.url + "/f", body=body())
    assert wire.connections == 1
    assert len(wire.requests) == 1


def test_a_request_that_cannot_be_repeated_does_not_trust_a_stale_connection(
    transport, monkeypatch
):
    monkeypatch.setattr(_builtin, "REUSE_WINDOW", 0.05)

    def script(request):
        # The first connection looks alive but no longer serves: a server or
        # a middlebox that forgot it while it was idle.
        if request.connection == 0 and request.body == b"two":
            return Drop()
        return response(body=b"ok")

    with Wire(script) as wire:
        send(transport, "POST", wire.url + "/f", body=b"one").read()
        threading.Event().wait(0.2)
        assert send(transport, "POST", wire.url + "/f", body=b"two").read() == b"ok"
    assert [r.connection for r in wire.requests] == [0, 1]
    assert [r.body for r in wire.requests] == [b"one", b"two"]


def test_a_request_that_cannot_be_repeated_reuses_a_connection_used_a_moment_ago(
    transport,
):
    with Wire() as wire:
        send(transport, "POST", wire.url + "/f", body=b"one").read()
        send(transport, "POST", wire.url + "/f", body=b"two").read()
    assert wire.connections == 1


def test_a_second_failure_is_not_retried_again(transport, unnoticed):
    # The resend goes to a server that also drops the request.
    def script(request):
        return (
            Reply(response(body=b"ok"), close=True)
            if request.connection == 0
            else Drop()
        )

    with Wire(script) as wire:
        get(transport, wire.url + "/").read()
        wait_until(lambda: 0 in wire.closed)
        with pytest.raises(ConnectionError):
            get(transport, wire.url + "/")
    assert wire.connections == 2


def test_a_timeout_on_a_reused_connection_is_not_sent_again(transport):
    def script(request):
        if request.connection == 0 and len(request.target) == 1:
            return response(body=b"ok")
        from http_wire import Hang

        return Hang()

    with Wire(script) as wire:
        get(transport, wire.url + "/").read()
        with pytest.raises(TimeoutError):
            transport.send("GET", wire.url + "/slow", headers={}, timeout=(5, 0.3))
    assert wire.connections == 1


# --- threads -----------------------------------------------------------------


def test_each_thread_has_its_own_connection(transport):
    with Wire() as wire:
        get(transport, wire.url + "/").read()
        worker = threading.Thread(target=lambda: get(transport, wire.url + "/").read())
        worker.start()
        assert wait_until(lambda: wire.connections == 2)
        # Its reply is read before the server goes away.
        worker.join()
        get(transport, wire.url + "/").read()
    assert wire.connections == 2


def test_the_connection_of_a_thread_closes_when_the_thread_ends(transport):
    with Wire() as wire:
        worker = threading.Thread(target=lambda: get(transport, wire.url + "/").read())
        worker.start()
        worker.join()
        assert wait_until(lambda: wire.closed == [0])


def test_a_thread_keeps_at_most_eight_idle_connections(transport, monkeypatch):
    wires = [Wire() for _ in range(9)]
    try:
        for wire in wires:
            get(transport, wire.url + "/").read()
        # The one used longest ago was closed to make room.
        assert wait_until(lambda: wires[0].closed == [0])
        assert all(wire.closed == [] for wire in wires[1:])
    finally:
        for wire in wires:
            wire.close()


# --- closing -----------------------------------------------------------------


def test_close_closes_idle_connections_and_may_be_called_twice():
    transport = HTTPTransport()
    with Wire() as wire:
        get(transport, wire.url + "/").read()
        transport.close()
        transport.close()
        assert wait_until(lambda: wire.closed == [0])
        # A closed transport connects afresh when used again.
        assert get(transport, wire.url + "/").read() == b"ok"
        transport.close()
    assert wire.connections == 2


def test_close_also_closes_the_connection_of_an_open_reply():
    transport = HTTPTransport()
    with Wire(lambda r: response(body=b"x" * 300000)) as wire:
        reply = get(transport, wire.url + "/", stream=True)
        reply.read(10)
        transport.close()
        assert wait_until(lambda: wire.closed == [0])
        with pytest.raises(OSError):
            reply.read()
        reply.close()


def test_a_transport_that_is_dropped_without_close_closes_its_connections():
    with Wire() as wire:
        transport = HTTPTransport()
        get(transport, wire.url + "/").read()
        del transport
        gc.collect()
        assert wait_until(lambda: wire.closed == [0])


def test_a_reply_that_is_dropped_unread_closes_its_connection():
    with Wire(lambda r: response(body=b"x" * 300000)) as wire:
        with HTTPTransport() as transport:
            reply = get(transport, wire.url + "/", stream=True)
            reply.read(10)
            del reply
            gc.collect()
            assert wait_until(lambda: wire.closed == [0])


def test_the_pool_forgets_nothing_it_still_needs_after_a_failed_request(transport):
    with Wire(lambda r: Drop()) as wire:
        for _ in range(3):
            with pytest.raises(ConnectionError):
                get(transport, wire.url + "/")
    assert wire.connections == 3
    assert transport._pool._open == set()


# --- a connection that cannot be used, found before anything is sent --------------


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_an_idle_connection_the_server_closed_is_replaced_before_sending(
    transport, method
):
    with Wire(closing_after_each_reply) as wire:
        send(transport, method, wire.url + "/f", body=b"data").read()
        assert wait_until(lambda: 0 in wire.closed)
        assert wait_until(lambda: _builtin._unusable(_idle_socket(transport)))
        assert send(transport, method, wire.url + "/f", body=b"data").read() == b"ok"
    # Each request reached the server once, the second on a new connection,
    # whatever its method.
    assert [r.connection for r in wire.requests] == [0, 1]


def _idle_socket(transport):
    (idle,) = transport._pool._idle.values()
    (connection,) = idle.values()
    return connection.http.sock


def test_bytes_the_server_sends_after_a_reply_are_not_the_next_reply(transport):
    def script(request):
        if request.connection == 0:
            return Reply(
                response(body=b"first"), later=response(body=b"POISON"), delay=0.2
            )
        return response(body=b"second")

    with Wire(script) as wire:
        assert get(transport, wire.url + "/").read() == b"first"
        threading.Event().wait(0.6)
        assert get(transport, wire.url + "/").read() == b"second"
    assert wire.connections == 2


# --- a closed connection stays closed ---------------------------------------------


def _run_as_the_exchange_begins(monkeypatch, action):
    """Calls `action(transport, connection)` where a request has its connection
    and is about to write: the instant a concurrent `close()` can land."""
    original = _builtin.HTTPTransport._exchange

    def exchange(self, connection, *args, **kwargs):
        action(self, connection)
        return original(self, connection, *args, **kwargs)

    monkeypatch.setattr(_builtin.HTTPTransport, "_exchange", exchange)


def _settle():
    threading.Event().wait(0.4)


def test_a_connection_closed_before_the_request_is_written_is_not_reopened(
    transport, monkeypatch
):
    _run_as_the_exchange_begins(monkeypatch, lambda t, connection: connection.close())
    with Wire() as wire:
        with pytest.raises(ConnectionError):
            get(transport, wire.url + "/")
        _settle()
    # The one connection the request made; no second, bare TCP one carried it.
    assert wire.connections == 1
    assert wire.requests == []


def test_a_transport_closed_before_the_request_is_written_does_not_send_it(
    transport, monkeypatch
):
    _run_as_the_exchange_begins(monkeypatch, lambda t, connection: t.close())
    with Wire() as wire:
        with pytest.raises(ConnectionError):
            transport.send(
                "GET",
                wire.url + "/",
                headers={"Authorization": "Bearer SECRET-TOKEN"},
                timeout=TIMEOUT,
            )
        _settle()
    assert wire.connections == 1
    assert wire.requests == []


def test_a_body_that_is_being_sent_when_the_transport_is_closed_is_not_finished_in_the_clear(
    tmp_path,
):
    from http_wire import make_authority

    authority = make_authority(tmp_path, "127.0.0.1", "localhost")
    transport = HTTPTransport(ssl_context=authority.client_context(), proxies={})

    class Upload:
        def __init__(self):
            self.blocks = 0

        def read(self, size=-1):
            self.blocks += 1
            if self.blocks == 3:
                transport.close()
            return b"BLOCK-%d;" % self.blocks if self.blocks < 6 else b""

    with Wire(tls=authority.server_context()) as wire:
        with pytest.raises(OSError):
            transport.send(
                "PUT", wire.url + "/f", headers={}, body=Upload(), timeout=TIMEOUT
            )
        _settle()
    # Nothing came back to the https port in the clear: that would be a
    # second connection, and the rest of the body would be on it.
    assert wire.connections == 1


def test_a_closed_transport_connects_afresh_when_it_is_used_again(transport):
    with Wire() as wire:
        get(transport, wire.url + "/").read()
        transport.close()
        assert get(transport, wire.url + "/").read() == b"ok"
    assert wire.connections == 2
