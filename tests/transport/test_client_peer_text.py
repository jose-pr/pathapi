"""Text from the other end that goes into an exception message: a reason
phrase, a `Location`. It is shown with its control characters escaped and cut
to a length, whichever transport sent the request."""

import pytest

from pathapi.http import HTTPClient, peer_text, raise_for_status, translate_errors
from transport_wire import Reply, Wire

pytestmark = pytest.mark.usefixtures("http_transport_kind")

ESC = "\x1b[31m"


def reply_with_reason(reason, status=500):
    raw = f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n".encode("latin-1")
    return lambda request: Reply(raw)


# --- the helper --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, shown",
    [
        ("plain", "plain"),
        ("café 日本", "café 日本"),
        ("a\x1b[31mb", "a\\x1b[31mb"),
        ("a\r\nb\tc\x00d", "a\\r\\nb\\tc\\x00d"),
        ("a‮b", "a\\u202eb"),  # a right-to-left override
        ("a b", "a\\u2028b"),  # a line separator
        ("a\x9bb", "a\\x9bb"),  # the C1 control that starts an escape sequence
    ],
)
def test_peer_text_escapes_what_a_terminal_would_act_on(text, shown):
    assert peer_text(text) == shown


def test_peer_text_is_cut_to_its_limit():
    assert peer_text("x" * 500) == "x" * 120 + "..."
    assert peer_text("x" * 20, limit=20) == "x" * 20
    assert peer_text("\x1b" * 500, limit=10) == "\\x1b" * 10 + "..."


# --- where the library shows it --------------------------------------------------


def test_a_reason_phrase_is_shown_escaped():
    with Wire(reply_with_reason(f"Server {ESC}Error")) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                with translate_errors(wire.url):
                    raise_for_status(client.request("GET", wire.url + "/x"))
    message = str(excinfo.value)
    assert "\x1b" not in message
    assert "Server \\x1b[31mError" in message


def test_a_long_reason_phrase_is_cut():
    with Wire(reply_with_reason("R" * 20000)) as wire:
        with HTTPClient() as client:
            with pytest.raises(OSError) as excinfo:
                with translate_errors(wire.url):
                    raise_for_status(client.request("GET", wire.url + "/x"))
    assert len(str(excinfo.value)) < 400


def test_a_redirect_that_is_refused_is_shown_cut_and_with_an_escaped_reason(
    http_transport,
):
    location = "/" + "a" * 5000

    def script(request):
        raw = (
            f"HTTP/1.1 302 Found {ESC}\r\nLocation: {location}\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode("latin-1")
        return Reply(raw)

    with Wire(script) as wire:
        client = HTTPClient(http_transport)
        with pytest.raises(OSError) as excinfo:
            client.request("PUT", wire.url + "/f", data=b"x")
    message = str(excinfo.value)
    assert "\x1b" not in message and "\\x1b[31m" in message
    assert len(message) < 500
