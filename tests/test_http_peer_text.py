"""Text from the other end that goes into an exception message: a reason
phrase, a `Location`, a rate-limit header, an `href`. It is shown with its
control characters escaped and cut to a length, whichever transport sent the
request."""

import xml.sax.saxutils

import pytest

from http_wire import Reply, Wire, response
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.http import HttpBackend
from pathlib_next.uri.schemes._http._headers import peer_text

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
        ("caf\u00e9 \u65e5\u672c", "caf\u00e9 \u65e5\u672c"),
        ("a\x1b[31mb", "a\\x1b[31mb"),
        ("a\r\nb\tc\x00d", "a\\r\\nb\\tc\\x00d"),
        ("a\u202eb", "a\\u202eb"),  # a right-to-left override
        ("a\u2028b", "a\\u2028b"),  # a line separator
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


def test_a_reason_phrase_is_shown_escaped(request):
    with Wire(reply_with_reason(f"Server {ESC}Error")) as wire:
        with pytest.raises(OSError) as excinfo:
            UriPath(wire.url + "/x").read_bytes()
    message = str(excinfo.value)
    assert "\x1b" not in message
    assert "Server \\x1b[31mError" in message


def test_a_long_reason_phrase_is_cut():
    with Wire(reply_with_reason("R" * 20000)) as wire:
        with pytest.raises(OSError) as excinfo:
            UriPath(wire.url + "/x").read_bytes()
    assert len(str(excinfo.value)) < 400


def test_the_reason_phrase_of_a_git_api_reply_is_shown_escaped():
    from pathlib_next.uri.schemes.github import GitHubPath, RepoBackend

    with Wire(reply_with_reason(f"Oops {ESC}", 500)) as wire:
        path = GitHubPath(
            "github://github.com/o/r/f.txt", backend=RepoBackend(api_base=wire.url)
        )
        with pytest.raises(OSError) as excinfo:
            path.read_bytes()
    assert "\x1b" not in str(excinfo.value)
    assert "Oops \\x1b[31m" in str(excinfo.value)


def test_a_rate_limit_header_is_shown_escaped_and_cut():
    from pathlib_next.uri.schemes.github import GitHubPath, RepoBackend

    def script(request):
        return response(
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": ESC + "9" * 500},
            reason="Forbidden",
        )

    with Wire(script) as wire:
        path = GitHubPath(
            "github://github.com/o/r/f.txt", backend=RepoBackend(api_base=wire.url)
        )
        with pytest.raises(OSError) as excinfo:
            path.read_bytes()
    message = str(excinfo.value)
    assert "\x1b" not in message and len(message) < 300


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
        backend = HttpBackend(http_transport)
        with pytest.raises(OSError) as excinfo:
            backend.request("PUT", wire.url + "/f", data=b"x")
    message = str(excinfo.value)
    assert "\x1b" not in message and "\\x1b[31m" in message
    assert len(message) < 500


def test_the_href_of_a_failed_member_is_shown_escaped_and_few_are_listed():
    members = "".join(
        f"<D:response><D:href>/d/%1B%5B31m{i}</D:href>"
        "<D:status>HTTP/1.1 423 Locked</D:status></D:response>"
        for i in range(200)
    )
    body = (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
        + members
        + "</D:multistatus>"
    )

    def script(request):
        return response(207, {"Content-Type": "application/xml"}, body.encode())

    with Wire(script) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/d/")
        with pytest.raises(OSError) as excinfo:
            path.rm(recursive=True)
    message = str(excinfo.value)
    assert "\x1b" not in message and "\\x1b[31m" in message
    assert "and 190 more" in message and len(message) < 1500
