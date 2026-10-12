"""TLS for the built-in transport: verification on by default, one explicit
switch to turn it off, and an `ssl_context` for a private authority."""

import socket
import ssl
import threading
import traceback

import pytest

from http_wire import Wire, make_authority, response
from pathlib_next.uri.schemes.http import HTTPTransport

TIMEOUT = (5, 5)


def get(transport, url, **kwargs):
    return transport.send("GET", url, headers={}, timeout=TIMEOUT, **kwargs)


@pytest.fixture
def authority(tmp_path):
    return make_authority(tmp_path, "localhost", "127.0.0.1")


@pytest.fixture
def secure(authority):
    with Wire(
        lambda r: response(body=b"secure"), tls=authority.server_context()
    ) as wire:
        yield wire


def test_a_server_signed_by_the_authority_of_the_context_is_trusted(authority, secure):
    with HTTPTransport(ssl_context=authority.client_context()) as transport:
        assert get(transport, secure.url + "/f").read() == b"secure"
    assert secure.requests[0].target == "/f"


def test_a_server_of_an_unknown_authority_is_refused_by_default(secure):
    with HTTPTransport() as transport:
        with pytest.raises(ssl.SSLCertVerificationError):
            get(transport, secure.url + "/")
    assert secure.requests == []


def test_verification_is_on_unless_it_is_turned_off(secure):
    with HTTPTransport(verify=True) as transport:
        with pytest.raises(ssl.SSLError):
            get(transport, secure.url + "/")


def test_verify_false_accepts_any_certificate(secure):
    with HTTPTransport(verify=False) as transport:
        assert get(transport, secure.url + "/").read() == b"secure"
    assert len(secure.requests) == 1


def test_an_ssl_context_wins_over_verify_false(secure):
    # A context that trusts nothing relevant still verifies.
    context = ssl.create_default_context()
    with HTTPTransport(ssl_context=context, verify=False) as transport:
        with pytest.raises(ssl.SSLCertVerificationError):
            get(transport, secure.url + "/")
    assert secure.requests == []


def test_a_certificate_for_another_name_is_refused_even_from_a_trusted_authority(
    tmp_path,
):
    other = make_authority(tmp_path, "elsewhere.test")
    with Wire(tls=other.server_context()) as wire:
        with HTTPTransport(ssl_context=other.client_context()) as transport:
            with pytest.raises(ssl.SSLCertVerificationError):
                get(transport, wire.url + "/")
    assert wire.requests == []


def test_a_server_that_does_not_speak_tls_is_an_ssl_error(authority):
    listener = socket.create_server(("127.0.0.1", 0))

    def answer_in_plain_text():
        conn, _ = listener.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        conn.close()

    server = threading.Thread(target=answer_in_plain_text, daemon=True)
    server.start()
    try:
        with HTTPTransport(ssl_context=authority.client_context()) as transport:
            with pytest.raises(ssl.SSLError):
                get(transport, f"https://127.0.0.1:{listener.getsockname()[1]}/")
    finally:
        server.join(timeout=5)
        listener.close()


def test_the_tls_connection_is_reused(authority, secure):
    with HTTPTransport(ssl_context=authority.client_context()) as transport:
        get(transport, secure.url + "/a").read()
        get(transport, secure.url + "/b").read()
    assert secure.connections == 1


def test_a_tls_failure_leaks_no_authorization_header(secure):
    with HTTPTransport() as transport:
        with pytest.raises(ssl.SSLError) as excinfo:
            transport.send(
                "GET",
                secure.url + "/",
                headers={"Authorization": "Bearer TOPSECRET"},
                timeout=TIMEOUT,
            )
    text = "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.tb)
    )
    assert "TOPSECRET" not in text
    assert "TOPSECRET" not in repr(excinfo.value)
