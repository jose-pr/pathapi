"""How much of a reply the library reads. A reply read whole for its status or
for a small document (a write's, a DELETE's, a PROPFIND's, a git API's) is
held to the limit a directory listing has, counted in DECODED bytes, whichever
transport sent it; one nobody looks at is read up to that limit and dropped;
and a connection whose reply was longer is not kept."""

import errno
import gzip

import pytest

from http_wire import Wire, response
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes import http as http_scheme
from pathlib_next.uri.schemes.http import HttpBackend
from waits import wait_until

pytestmark = pytest.mark.usefixtures("http_transport_kind")

CAP = 64 * 1024
INFLATED = bytes(1024 * 1024)  # 16 times the cap, a kilobyte on the wire
BOMB = gzip.compress(INFLATED)
CODED = {"Content-Encoding": "gzip"}


@pytest.fixture(autouse=True)
def small_cap(monkeypatch):
    monkeypatch.setattr(http_scheme, "MAX_LISTING_BYTES", CAP)


def long_reply(status=200, extra=None):
    """A reply whose body is three times the limit on the wire as well."""
    return response(status, extra or {}, b"x" * (3 * CAP))


def bomb(status=200, extra=None):
    return response(status, {**CODED, **(extra or {})}, BOMB)


# --- replies nobody looks at ---------------------------------------------------------


def test_the_reply_to_a_write_is_dropped_and_its_connection_is_not_kept():
    with Wire(lambda r: long_reply(201)) as wire:
        path = UriPath(wire.url + "/file")
        path.write_bytes(b"data")  # the status was fine: nothing is raised
        assert wait_until(lambda: wire.closed == [0])
        path.write_bytes(b"again")
    assert wire.connections == 2
    assert [r.body for r in wire.requests] == [b"data", b"again"]


def test_the_reply_to_a_delete_is_dropped_and_its_connection_is_not_kept():
    with Wire(lambda r: long_reply(200)) as wire:
        path = UriPath(wire.url + "/file")
        path._delete()
        assert wait_until(lambda: wire.closed == [0])
    assert [r.method for r in wire.requests] == ["DELETE"]


def test_a_small_reply_to_a_write_is_read_so_the_connection_serves_the_next_one():
    with Wire(lambda r: response(201, {}, b"x" * 1000)) as wire:
        path = UriPath(wire.url + "/file")
        path.write_bytes(b"one")
        path.write_bytes(b"two")
    assert wire.connections == 1


def test_an_error_reply_with_a_long_body_is_dropped_too():
    with Wire(lambda r: long_reply(500)) as wire:
        with pytest.raises(OSError):
            UriPath(wire.url + "/file").write_bytes(b"data")
        assert wait_until(lambda: wire.closed == [0])


def test_a_coded_reply_to_a_write_is_not_inflated_whole():
    with Wire(lambda r: bomb(201)) as wire:
        UriPath(wire.url + "/file").write_bytes(b"data")
    assert [r.body for r in wire.requests] == [b"data"]


def test_the_reply_to_an_append_is_dropped_in_both_modes():
    with Wire(lambda r: bomb(201) if r.method != "GET" else response(404)) as wire:
        with UriPath(wire.url + "/log").open("ab") as stream:
            stream.write(b"more")
        assert wait_until(lambda: len(wire.requests) == 2)
    assert [r.method for r in wire.requests] == ["GET", "PUT"]


# --- replies read for what they say ----------------------------------------------------


def test_a_propfind_reply_over_the_limit_is_efbig_counted_in_decoded_bytes():
    with Wire(lambda r: bomb(207, {"Content-Type": "application/xml"})) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/dir/")
        with pytest.raises(OSError) as excinfo:
            path.stat()
        # A kilobyte on the wire, so a check of the stated length would pass.
        assert len(BOMB) < CAP
    assert excinfo.value.errno == errno.EFBIG
    assert str(path) in str(excinfo.value)
    assert str(CAP) in str(excinfo.value)


def test_a_propfind_reply_that_is_plain_and_too_long_is_efbig_as_well():
    with Wire(lambda r: long_reply(207)) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/dir/")
        with pytest.raises(OSError) as excinfo:
            path.stat()
        # Past the limit, the rest of the body is not waited for.
        assert wait_until(lambda: wire.closed == [0])
    assert excinfo.value.errno == errno.EFBIG


def test_a_propfind_reply_within_the_limit_is_read(monkeypatch):
    body = (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response>'
        "<D:href>/dir/f</D:href><D:propstat><D:prop><D:resourcetype/>"
        "<D:getcontentlength>5</D:getcontentlength></D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>"
    ).encode()
    with Wire(lambda r: response(207, {}, body)) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/dir/f")
        assert path.stat().st_size == 5
        assert path.stat().st_size == 5
    assert wire.connections == 1


def test_the_limit_is_read_each_time_so_raising_it_lets_a_larger_reply_through(
    monkeypatch,
):
    padded = (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response>'
        "<D:href>/dir/f</D:href><D:propstat><D:prop><D:resourcetype/>"
        "<D:getcontentlength>5</D:getcontentlength></D:prop>"
        "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        "</D:multistatus>" + " " * (2 * CAP)
    ).encode()
    with Wire(lambda r: response(207, {}, padded)) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/dir/f")
        with pytest.raises(OSError):
            path.stat()
        monkeypatch.setattr(http_scheme, "MAX_LISTING_BYTES", 4 * CAP)
        assert path.stat().st_size == 5


def test_a_dav_delete_with_a_207_reply_over_the_limit_is_efbig():
    with Wire(lambda r: bomb(207, {"Content-Type": "application/xml"})) as wire:
        path = UriPath("dav" + wire.url[len("http") :] + "/dir/")
        with pytest.raises(OSError) as excinfo:
            path.rm(recursive=True)
    assert excinfo.value.errno == errno.EFBIG


def test_a_git_api_reply_over_the_limit_is_efbig_and_a_file_is_not_limited():
    from pathlib_next.uri.schemes.github import GitHubPath, RepoBackend

    raw = {
        "Content-Type": "application/vnd.github.raw+json",
        "X-GitHub-Media-Type": "github.v3; param=raw",
    }
    content = b"c" * (3 * CAP)

    def script(request):
        if request.target.startswith("/repos/o/r/contents/dir"):
            return bomb(200, {"Content-Type": "application/json"})
        return response(200, raw, content)

    with Wire(script) as wire:
        backend = RepoBackend(api_base=wire.url)
        directory = GitHubPath("github://github.com/o/r/dir", backend=backend)
        with pytest.raises(OSError) as excinfo:
            directory.stat()
        assert excinfo.value.errno == errno.EFBIG
        assert directory.exists() is False
        # The content of a file is what was asked for, not a document.
        file = GitHubPath("github://github.com/o/r/file.bin", backend=backend)
        assert file.read_bytes() == content


def test_a_gitlab_api_reply_over_the_limit_is_efbig():
    from pathlib_next.uri.schemes.gitlab import GitLabPath, RepoBackend

    with Wire(lambda r: bomb(200, {"Content-Type": "application/json"})) as wire:
        backend = RepoBackend(api_base=wire.url + "/api/v4")
        path = GitLabPath("gitlab://gitlab.com/o/r", backend=backend)
        with pytest.raises(OSError) as excinfo:
            path.stat()
    assert excinfo.value.errno == errno.EFBIG


def test_the_backend_reads_a_reply_whole_only_up_to_the_limit(http_transport):
    with Wire(lambda r: bomb(200)) as wire:
        backend = HttpBackend(http_transport)
        with pytest.raises(OSError) as excinfo:
            backend.request("GET", wire.url + "/x")
        assert excinfo.value.errno == errno.EFBIG
        assert wire.url in str(excinfo.value)
        # A caller that reads the body itself is not limited.
        with backend.request("GET", wire.url + "/x", stream=True) as streamed:
            assert len(streamed.read()) == len(INFLATED)
