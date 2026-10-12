"""`Authorization` belongs to the origin a request was made for. Every way a
path asks again after a redirect (`stat()` and what is built on it, a
patch-mode append, the git APIs) leaves it off a request to another scheme,
host or port, whichever transport sends, and keeps it within the origin."""

import pytest

from http_fakes import on_current_transport
from http_wire import Wire, make_authority, response
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.http import HttpPath, HTTPTransport

pytestmark = pytest.mark.usefixtures("http_transport_kind")

TOKEN = "Bearer SECRET-TOKEN"


def sees_no_credential(wire):
    return [r.header("Authorization") for r in wire.requests] == [None] * len(
        wire.requests
    )


@pytest.fixture
def origins():
    """`home` redirects every HEAD and GET to `away`; both answer a file of
    five bytes, and `home` takes a write or a DELETE."""
    with Wire(
        lambda r: response(200, {"Content-Type": "text/plain"}, b"hello")
    ) as away:

        def home_script(request):
            if request.method in ("HEAD", "GET"):
                return response(
                    302, {"Location": away.url + "/elsewhere"}, reason="Found"
                )
            return response(204, reason="No Content")

        with Wire(home_script) as home:
            yield home, away


def credentialed(wire, how):
    """A path on `wire` that is sent with a credential: a backend header, or
    the userinfo of its URL."""
    if how == "header":
        return on_current_transport(
            UriPath(wire.url + "/f"), headers={"Authorization": TOKEN}
        )
    return on_current_transport(UriPath(f"http://user:s3cr3t@127.0.0.1:{wire.port}/f"))


def _open_exclusively(path):
    with pytest.raises(FileExistsError):
        path.open("x")


OPERATIONS = {
    "stat": lambda p: p.stat(),
    "exists": lambda p: p.exists(),
    "is_dir": lambda p: p.is_dir(),
    "is_file": lambda p: p.is_file(),
    "unlink": lambda p: p.unlink(),
    "read_bytes": lambda p: p.read_bytes(),
    "open x": _open_exclusively,
}


@pytest.mark.parametrize("how", ["header", "userinfo"])
@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_redirect_to_another_origin_does_not_carry_authorization(
    origins, operation, how
):
    home, away = origins
    OPERATIONS[operation](credentialed(home, how))
    # The redirect was followed, and the other origin was sent no credential.
    assert away.requests
    assert sees_no_credential(away)
    # The credential does reach the origin it was configured for.
    assert home.requests[0].header("Authorization") is not None


def test_a_patch_mode_append_asks_the_other_origin_without_authorization(origins):
    home, away = origins
    path = on_current_transport(
        UriPath(home.url + "/f"),
        headers={"Authorization": TOKEN},
        append_mode="patch",
    )
    with path.open("ab") as stream:
        stream.write(b"more")
    assert away.requests and sees_no_credential(away)
    # The PATCH goes to the path's own origin, with the credential, at the
    # size the other origin stated.
    patch = [r for r in home.requests if r.method == "PATCH"][0]
    assert patch.header("Authorization") == TOKEN
    assert patch.header("Content-Range") == "bytes 5-8/*"


def test_a_redirect_within_the_origin_keeps_authorization(http_transport):
    def script(request):
        if request.target == "/d":
            return response(301, {"Location": "/d/"}, reason="Moved")
        return response(200, {"Content-Type": "text/html"}, b"<html></html>")

    with Wire(script) as wire:
        path = on_current_transport(
            UriPath(wire.url + "/d"), headers={"Authorization": TOKEN}
        )
        assert path.stat().is_dir()
    assert [r.target for r in wire.requests] == ["/d", "/d/"]
    assert [r.header("Authorization") for r in wire.requests] == [TOKEN, TOKEN]


def test_a_second_redirect_back_to_the_origin_does_not_bring_it_back(http_transport):
    with Wire() as wire:
        with Wire(lambda r: response(302, {"Location": wire.url + "/home"})) as away:

            def script(request):
                if request.target == "/start":
                    return response(302, {"Location": away.url + "/away"})
                return response(200, {}, b"ok")

            wire.script = script
            path = on_current_transport(
                UriPath(wire.url + "/start"), headers={"Authorization": TOKEN}
            )
            path.stat()
    assert [r.header("Authorization") for r in wire.requests] == [TOKEN, None]
    assert sees_no_credential(away)


def test_a_redirect_from_https_to_plain_http_does_not_carry_authorization(tmp_path):
    authority = make_authority(tmp_path, "127.0.0.1", "localhost")
    with Wire(lambda r: response(200, {}, b"plain")) as plain:
        secure_script = lambda r: response(  # noqa: E731
            302, {"Location": plain.url + "/downgraded"}, reason="Found"
        )
        with Wire(secure_script, tls=authority.server_context()) as secure:
            transport = HTTPTransport(ssl_context=authority.client_context())
            path = HttpPath(secure.url + "/f").with_transport(
                transport, headers={"Authorization": TOKEN}
            )
            try:
                assert path.stat().st_size == 5
                assert path.exists()
            finally:
                transport.close()
    assert secure.requests[0].header("Authorization") == TOKEN
    assert plain.requests and sees_no_credential(plain)


def test_the_git_apis_send_their_token_to_their_own_origin_only():
    from pathlib_next.uri.schemes.github import GitHubPath, RepoBackend

    raw = {
        "X-GitHub-Media-Type": "github.v3; param=raw",
        "Content-Type": "application/vnd.github.raw+json",
    }
    with Wire(lambda r: response(200, raw, b"abc")) as away:

        def home_script(request):
            return response(302, {"Location": away.url + "/elsewhere"}, reason="Found")

        with Wire(home_script) as home:
            backend = RepoBackend(token="T0KEN", api_base=home.url)
            path = GitHubPath("github://github.com/o/r/f.txt", backend=backend)
            assert path.exists()
            assert path.read_bytes() == b"abc"
    assert home.requests[0].header("Authorization") == "Bearer T0KEN"
    assert away.requests and sees_no_credential(away)
