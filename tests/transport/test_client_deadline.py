"""`deadline`: the seconds a whole request may take, redirects and the reading
of the body included, where `timeout` bounds each wait."""

import socket
import threading
import time

import pytest

from pathapi.http import HTTPClient, HTTPResponse
from transport_wire import Wire, response


class _Trickle:
    """A loopback origin that promises a long body and sends one byte of it
    every `interval` seconds until it is closed."""

    def __init__(self, interval=0.02):
        self.interval = interval
        self.stop = threading.Event()
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}/"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.listener.settimeout(0.1)
        while not self.stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except OSError:
                continue
            threading.Thread(target=self._trickle, args=(sock,), daemon=True).start()

    def _trickle(self, sock):
        try:
            sock.recv(65536)
            sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 300\r\n\r\n")
            while not self.stop.is_set():
                sock.sendall(b"x")
                time.sleep(self.interval)
        except OSError:
            pass
        finally:
            sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.stop.set()
        self.listener.close()


class _Slow:
    """A transport that takes `delay` seconds per send, answers with a 302
    while `hops` sends have not been made and then with a 200, and records
    the timeout each send was given."""

    def __init__(self, delay=0.0, hops=0):
        self.delay = delay
        self.hops = hops
        self.timeouts = []

    def send(self, method, url, *, headers=None, body=None, timeout=None, stream=False):
        self.timeouts.append(timeout)
        time.sleep(self.delay)
        if len(self.timeouts) <= self.hops:
            return HTTPResponse(
                302, "Found", {"Location": f"http://h/{len(self.timeouts)}"}, url
            )
        return HTTPResponse(200, "OK", {}, url, b"ok")

    def close(self):
        pass


class _SlowBody:
    def __init__(self, parts, delay):
        self.parts, self.delay = list(parts), delay

    def read(self, amount):
        time.sleep(self.delay)
        return self.parts.pop(0) if self.parts else b""

    def close(self):
        pass


@pytest.mark.parametrize("value", [0, -1, -0.5, float("nan")])
def test_a_deadline_that_is_not_positive_is_a_value_error(value):
    with Wire() as wire:
        with pytest.raises(ValueError):
            HTTPClient(deadline=value)
        with pytest.raises(ValueError):
            HTTPClient().request("GET", wire.url, deadline=value)
        assert wire.requests == []


@pytest.mark.parametrize("value", ["1", True, (1, 2), b"1"])
def test_a_deadline_that_is_not_a_number_is_a_type_error(value):
    with pytest.raises(TypeError):
        HTTPClient(deadline=value)
    with pytest.raises(TypeError):
        HTTPClient(_Slow()).request("GET", "http://h/", deadline=value)


def test_a_client_has_no_deadline_unless_given_one():
    assert HTTPClient().deadline is None
    assert HTTPClient(deadline=3).deadline == 3


def test_a_server_that_trickles_its_body_is_cut_off_at_the_deadline():
    with _Trickle() as origin:
        with HTTPClient(timeout=(2, 2)) as client:
            started = time.monotonic()
            with pytest.raises(TimeoutError, match="deadline"):
                client.request("GET", origin.url, deadline=0.3)
            assert time.monotonic() - started < 2


def test_a_streamed_body_is_cut_off_at_the_deadline_between_reads():
    with _Trickle() as origin:
        with HTTPClient(timeout=(2, 2)) as client:
            started = time.monotonic()
            with client.request("GET", origin.url, stream=True, deadline=0.3) as resp:
                with pytest.raises(TimeoutError, match="deadline"):
                    while resp.read(10):
                        pass
            assert time.monotonic() - started < 2


def test_the_deadline_of_the_client_applies_and_a_call_may_replace_it():
    with _Trickle() as origin:
        with HTTPClient(timeout=(2, 2), deadline=0.2) as client:
            with pytest.raises(TimeoutError):
                client.request("GET", origin.url)
            with pytest.raises(TimeoutError):
                client.request("GET", origin.url, deadline=0.3)
            with client.request("GET", origin.url, stream=True, deadline=None) as resp:
                assert resp.read(5) == b"xxxxx"


def test_a_request_without_a_deadline_is_sent_with_its_timeout_as_before():
    transport = _Slow()
    HTTPClient(transport, timeout=(10, 60)).request("GET", "http://h/")
    assert transport.timeouts == [(10, 60)]


def test_each_wait_is_capped_at_the_time_that_remains():
    transport = _Slow()
    client = HTTPClient(transport, timeout=(10, 60))
    client.request("GET", "http://h/", deadline=5)
    client.request("GET", "http://h/", timeout=None, deadline=5)
    client.request("GET", "http://h/", timeout=2, deadline=5)
    (connect, read), unlimited, short = transport.timeouts
    assert 0 < connect <= 5 and 0 < read <= 5
    assert 0 < unlimited <= 5
    assert short == 2


def test_a_chain_of_redirects_shares_one_deadline():
    transport = _Slow(delay=0.15, hops=10)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        HTTPClient(transport).request("GET", "http://h/", deadline=0.5)
    assert time.monotonic() - started < 1.5
    assert 2 <= len(transport.timeouts) <= 5
    firsts = [t[0] for t in transport.timeouts]
    assert firsts == sorted(firsts, reverse=True)


def test_a_body_that_is_read_whole_is_cut_off_at_the_deadline():
    body = _SlowBody([b"a", b"b", b"c", b"d", b"e"], delay=0.2)
    transport = _Slow()
    transport.send = lambda *a, **k: HTTPResponse(200, "OK", {}, "http://h/", body)
    with pytest.raises(TimeoutError, match="deadline"):
        HTTPClient(transport).request("GET", "http://h/", deadline=0.5)


def test_a_deadline_that_has_not_passed_changes_nothing():
    with Wire(lambda request: response(body=b"hello")) as wire:
        with HTTPClient(deadline=30) as client:
            assert client.request("GET", wire.url).read() == b"hello"
