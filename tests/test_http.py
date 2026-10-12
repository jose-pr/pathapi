"""HttpPath against a real stdlib ThreadingHTTPServer (see conftest.py's
http_server fixture) serving fixture_tree's directory listing. Skipped
entirely if the http extra isn't installed.
"""

import http.server
import io
import typing

import pytest

pytest.importorskip("requests")

from http_fakes import FakeTransport, reply
from pathlib_next.uri import UriPath


class _Request(typing.NamedTuple):
    method: str
    path: str
    headers: dict
    body: bytes


class _Site:
    """A loopback site that logs every request in `seen`. `serve()` puts a
    resource at a path: GET and HEAD answer it, DELETE removes it and every
    other method is accepted. A path that is not served is a 404."""

    def __init__(self):
        self.seen: "list[_Request]" = []
        self.resources: "dict[str, tuple[int, str, bytes]]" = {}
        self.base = ""

    def serve(self, path, body=b"", content_type="text/plain", status=200):
        self.resources[path] = (status, content_type, body)

    def index(self, path, *rows):
        """A directory index of `rows` (the text between `<pre>` tags)."""
        html = f"<html><body><pre>{''.join(rows)}</pre></body></html>"
        self.serve(path, html.encode(), "text/html; charset=utf-8")

    def sent(self, *methods):
        return [r for r in self.seen if r.method in methods]

    def url(self, path):
        return self.base + path


def _site_handler(site):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            site.seen.append(
                _Request(self.command, self.path, dict(self.headers.items()), body)
            )
            payload, content_type = b"", None
            if self.command in ("GET", "HEAD"):
                status, content_type, payload = site.resources.get(
                    self.path, (404, "text/plain", b"")
                )
            elif self.command == "DELETE":
                status = 204 if site.resources.pop(self.path, None) else 404
            else:
                status = 204
            self.send_response(status)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_GET = do_HEAD = do_DELETE = do_PUT = do_PATCH = do_POST = _serve

    return Handler


@pytest.fixture
def site(serve_http):
    """A `_Site` served on the loopback interface."""
    site = _Site()
    with serve_http(_site_handler(site)) as base:
        site.base = base
        yield site


def test_iterdir_lists_files_and_dirs(http_server):
    root = UriPath(f"{http_server}/")
    names = {p.name for p in root.iterdir()}
    # B-adjacent regression: subdirectory entries used to get name == ""
    # because directory-listing entries carry a trailing "/" that wasn't
    # stripped before deriving .name. iterdir() (unlike glob()) doesn't
    # filter hidden entries, matching pathlib.Path.iterdir().
    assert names == {"a.txt", "b.py", ".hidden.txt", "sub", "empty_dir"}


def test_iterdir_subdir_is_dir_not_file(http_server):
    root = UriPath(f"{http_server}/")
    sub = next(p for p in root.iterdir() if p.name == "sub")
    assert sub.is_dir()
    assert not sub.is_file()


def test_file_is_file_not_dir(http_server):
    root = UriPath(f"{http_server}/")
    f = next(p for p in root.iterdir() if p.name == "a.txt")
    assert f.is_file()
    assert not f.is_dir()


def test_read_text(http_server):
    p = UriPath(f"{http_server}/a.txt")
    assert p.read_text() == "a"


def test_stat_size(http_server):
    p = UriPath(f"{http_server}/a.txt")
    st = p.stat()
    assert st.st_size == 1  # fixture_tree's a.txt content is "a"


def test_stat_missing_raises_filenotfounderror(http_server):
    p = UriPath(f"{http_server}/does-not-exist.txt")
    with pytest.raises(FileNotFoundError):
        p.stat()


def test_exists_false_for_missing(http_server):
    p = UriPath(f"{http_server}/does-not-exist.txt")
    assert not p.exists()


def test_dir_without_trailing_slash_is_dir(http_server):
    # The server 301-redirects a bare "/sub" request to "/sub/" --
    # is_dir() must follow that and report True.
    p = UriPath(f"{http_server}/sub")
    assert p.is_dir()


def test_recursive_glob(http_server):
    root = UriPath(f"{http_server}/")
    names = {p.name for p in root.glob("**/*.py")}
    assert names == {"b.py", "c.py", "d.py"}


def test_http_exception_translation(http_server):
    # Test standard exception translation on real HTTP errors.
    # 404 client error -> FileNotFoundError
    with pytest.raises(FileNotFoundError):
        UriPath(f"{http_server}/does-not-exist.txt").read_text()

    # 405 Method Not Allowed / 501 Not Implemented -> PermissionError or NotImplementedError.
    # The stdlib ThreadingHTTPServer returns 501 or 405 for DELETE/PUT.
    p = UriPath(f"{http_server}/a.txt")
    with pytest.raises(PermissionError):
        p.unlink()


def test_http_write_put_default():
    transport = FakeTransport(default=reply(200))
    p = UriPath("http://example.com/file.txt").with_transport(transport)
    p.write_text("hello")

    assert [(s.method, s.url, s.body) for s in transport.sent] == [
        ("PUT", "http://example.com/file.txt", b"hello")
    ]


def test_http_write_post_configured():
    transport = FakeTransport(default=reply(200))
    p = UriPath("http://example.com/file.txt").with_transport(
        transport, write_method="POST"
    )
    p.write_text("world")

    assert [(s.method, s.url, s.body) for s in transport.sent] == [
        ("POST", "http://example.com/file.txt", b"world")
    ]


def test_http_unlink(site):
    site.serve("/file.txt", b"hello")

    UriPath(site.url("/file.txt")).unlink()

    assert [(r.method, r.path) for r in site.sent("DELETE")] == [
        ("DELETE", "/file.txt")
    ]


def test_http_unlink_of_a_missing_path_raises(site):
    with pytest.raises(FileNotFoundError):
        UriPath(site.url("/gone.txt")).unlink()
    assert [r.path for r in site.sent("DELETE")] == ["/gone.txt"]


def test_http_unlink_missing_ok_accepts_a_delete_answered_404(site):
    UriPath(site.url("/gone.txt")).unlink(missing_ok=True)
    assert [r.path for r in site.sent("DELETE")] == ["/gone.txt"]


def test_http_rmdir_empty(site):
    site.index("/dir/")

    UriPath(site.url("/dir/")).rmdir()

    assert [r.path for r in site.sent("DELETE")] == ["/dir/"]
    # The listing was read before the directory was removed.
    methods = [r.method for r in site.seen]
    assert methods.index("GET") < methods.index("DELETE")


def test_http_rmdir_not_empty(site):
    import errno

    site.index("/dir/", '<a href="a.txt">a.txt</a>\n')

    with pytest.raises(OSError) as excinfo:
        UriPath(site.url("/dir/")).rmdir()

    assert excinfo.value.errno == errno.ENOTEMPTY
    assert site.sent("DELETE") == []


def test_http_unlink_on_directory_sends_no_delete(site):
    import errno

    site.index("/dir/", '<a href="a.txt">a.txt</a>\n')

    with pytest.raises(IsADirectoryError) as excinfo:
        UriPath(site.url("/dir/")).unlink()

    assert excinfo.value.errno == errno.EISDIR
    assert site.sent("DELETE") == []


def test_http_rmdir_ignores_dot_entries_in_listing(site):
    # An empty directory whose listing carries its own "."/".." rows is
    # still empty -- rmdir() must DELETE it, not raise ENOTEMPTY.
    site.index("/dir/", '<a href="..">..</a>\n', '<a href="./">./</a>\n')

    UriPath(site.url("/dir/")).rmdir()

    assert [r.path for r in site.sent("DELETE")] == ["/dir/"]


def test_http_listing_leaves_out_a_name_that_is_not_a_safe_component(site):
    # A NUL ends a name for most programs: "x<NUL>y" is not a child.
    site.index("/d/", '<a href="x%00y">x</a>\n', '<a href="ok.txt">ok.txt</a>\n')

    children = [child.name for child in UriPath(site.url("/d/")).iterdir()]

    assert children == ["ok.txt"]


@pytest.mark.parametrize("content_type", ["text/plain", "text/html"])
def test_http_rmdir_on_file_raises_notadirectoryerror(site, content_type):
    # rmdir() on a *file* must raise NotADirectoryError (matching
    # os.rmdir()'s ENOTDIR contract), not silently DELETE it -- a page served
    # as HTML parses to zero entries, exactly like an empty directory's index.
    site.serve("/file", b"<html><body></body></html>", content_type)

    with pytest.raises(NotADirectoryError):
        UriPath(site.url("/file")).rmdir()

    assert site.sent("DELETE") == []


def test_http_write_stream_marks_closed_on_upload_failure():
    # A failed close() must not leave the stream open -- otherwise a
    # later close() (context-manager __exit__, or GC via
    # IOBase.__del__) silently retries the PUT.
    from pathlib_next.uri.schemes.http import HttpWriteStream

    transport = FakeTransport(default=reply(500))
    p = UriPath("http://example.com/f.txt").with_transport(transport)
    stream = HttpWriteStream(p)
    stream.write(b"x")

    with pytest.raises(OSError):
        stream.close()
    assert stream.closed

    # a second close() must be a no-op, not another PUT attempt
    stream.close()
    assert len(transport.sent) == 1


def _MockHttpResponse(status_code, text="", headers=None, url=None):
    return reply(
        status_code,
        text.encode(),
        headers,
        reason="OK" if status_code < 400 else "Not Found",
        url=url or "http://example.com/file.txt",
    )


def test_listdir_retries_with_trailing_slash_on_404():
    # Real servers (incl. this repo's http_server fixture) 301-redirect a
    # slash-less directory GET, and the backend follows that automatically --
    # but a non-redirecting server/proxy would just 404 the bare path. This
    # mirrors stat()'s [no-slash, with-slash] retry so _listdir() is
    # robust to that case too (kept minimal by design).
    listing_html = (
        "<html><body><pre>"
        '<a href="../">../</a>\n'
        '<a href="a.txt">a.txt</a>  11-Jul-2026 10:23   1K\n'
        "</pre></body></html>"
    )

    def answer(method, url, **kwargs):
        # `url=`: a listing is scoped by the URL that answered it.
        if url.endswith("/"):
            return _MockHttpResponse(200, listing_html, url=url)
        return _MockHttpResponse(404, url=url)

    transport = FakeTransport(handler=answer)
    p = UriPath("http://example.com/sub").with_transport(transport)
    names = {e.name for e in p._listdir()}
    assert names == {"a.txt"}


def test_listdir_no_retry_when_already_trailing_slash():
    # A 404 on a path that already ends in "/" is a real 404, not a
    # missing-redirect case -- must not retry (would just repeat the same
    # request) and must propagate FileNotFoundError.
    transport = FakeTransport(default=_MockHttpResponse(404))
    p = UriPath("http://example.com/sub/").with_transport(transport)
    with pytest.raises(FileNotFoundError):
        p._listdir()
    assert [s.url for s in transport.sent] == ["http://example.com/sub/"]


# --- Append mode tests ---


def test_http_append_rewrite_mode_nonexistent_file(http_writable_server):
    """Append to non-existent file in rewrite mode (default).
    Should behave like write mode."""
    p = UriPath(f"{http_writable_server}/append_new.txt")
    with p.open("ab") as f:
        f.write(b"hello")
    assert p.read_bytes() == b"hello"


def test_http_append_rewrite_mode_existing_file(http_writable_server):
    """Append to existing file in rewrite mode (default).
    Should GET existing, then PUT old+new."""
    p = UriPath(f"{http_writable_server}/append_existing.txt")
    # Write initial content
    p.write_bytes(b"hello")
    # Append to it
    with p.open("ab") as f:
        f.write(b" world")
    assert p.read_bytes() == b"hello world"


def test_http_append_rewrite_mode_multiple_writes(http_writable_server):
    """Multiple writes in append mode should concatenate."""
    p = UriPath(f"{http_writable_server}/append_multi.txt")
    p.write_bytes(b"a")
    with p.open("ab") as f:
        f.write(b"b")
        f.write(b"c")
    assert p.read_bytes() == b"abc"


@pytest.fixture
def patch_mode_requests(monkeypatch):
    """Record every request method sent through `requests`, passing each on
    to the real server unchanged."""
    import requests

    real_request = requests.Session.request
    methods = []

    def request(self, method, url, *args, **kwargs):
        methods.append(method)
        return real_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", request)
    return methods


def _patch_mode(url):
    import requests

    return UriPath(url).with_session(requests.Session(), append_mode="patch")


def test_http_append_patch_mode_existing_file_real_server(
    http_writable_server, patch_mode_requests
):
    # conftest's _WritableHandler.do_PATCH writes at the Content-Range
    # offset, so a wrong offset corrupts the persisted bytes.
    p = _patch_mode(f"{http_writable_server}/patch_existing.txt")
    p.write_bytes(b"hello")
    patch_mode_requests.clear()
    with p.open("ab") as f:
        f.write(b" wor")
        f.write(b"ld")
    assert "PATCH" in patch_mode_requests and "GET" not in patch_mode_requests
    assert p.read_bytes() == b"hello world"
    with p.open("ab") as f:
        f.write(b"!")
    assert p.read_bytes() == b"hello world!"


def test_http_append_patch_mode_new_file_real_server(
    http_writable_server, patch_mode_requests
):
    p = _patch_mode(f"{http_writable_server}/patch_new.txt")
    with p.open("ab") as f:
        f.write(b"abc")
    assert patch_mode_requests.count("PATCH") == 1
    assert p.read_bytes() == b"abc"


def test_http_append_patch_mode_empty_append_real_server(
    http_writable_server, patch_mode_requests
):
    # pathlib's open("ab") with nothing written creates a missing file and
    # leaves an existing one alone; neither sends an (invalid) empty range.
    new = _patch_mode(f"{http_writable_server}/patch_empty_new.txt")
    with new.open("ab"):
        pass
    assert new.read_bytes() == b""

    existing = _patch_mode(f"{http_writable_server}/a.txt")
    with existing.open("ab"):
        pass
    assert existing.read_bytes() == b"a"
    assert "PATCH" not in patch_mode_requests


def test_http_append_patch_mode_configured():
    """Test PATCH mode with Content-Range header."""

    def answer(method, url, **kwargs):
        if method == "HEAD":
            return _MockHttpResponse(200, "", headers={"Content-Length": "16"}, url=url)
        elif method == "PATCH":
            return _MockHttpResponse(204, url=url)
        else:
            return _MockHttpResponse(200, url=url)

    transport = FakeTransport(handler=answer)
    p = UriPath("http://example.com/file.txt").with_transport(
        transport, append_mode="patch"
    )

    with p.open("ab") as f:
        f.write(b"new content")

    # One HEAD for the size (the start offset), then exactly the new bytes
    # at that offset -- never a GET + full rewrite.
    assert [(s.method, s.body) for s in transport.sent] == [
        ("HEAD", None),
        ("PATCH", b"new content"),
    ]
    assert transport.sent[1].headers["Content-Range"] == "bytes 16-26/*"


def test_http_append_patch_mode_rejection():
    """PATCH mode should raise if server rejects with 405."""

    def answer(method, url, **kwargs):
        if method == "PATCH":
            return _MockHttpResponse(405)
        elif method == "HEAD":
            return _MockHttpResponse(200, "", headers={"Content-Length": "15"})
        elif method == "GET":
            return _MockHttpResponse(200, "existing content")
        else:
            return _MockHttpResponse(200)

    p = UriPath("http://example.com/file.txt").with_transport(
        FakeTransport(handler=answer), append_mode="patch"
    )

    # Should raise PermissionError on PATCH rejection (405 -> PermissionError)
    with pytest.raises(PermissionError):
        with p.open("ab") as f:
            f.write(b"new content")


def test_http_append_write_stream_marks_closed_on_failure():
    """Append stream must mark itself closed even on failed close()."""
    from pathlib_next.uri.schemes.http import HttpAppendStream

    # The GET for the initial content in rewrite mode finds no file; the PUT
    # that closes the stream fails.
    transport = FakeTransport(
        {("GET", "http://example.com/f.txt"): reply(404)}, default=reply(500)
    )
    p = UriPath("http://example.com/f.txt").with_transport(transport)
    stream = HttpAppendStream(p)
    stream.write(b"x")

    with pytest.raises(OSError):
        stream.close()
    assert stream.closed

    # Second close() must be a no-op
    stream.close()
    assert [s.method for s in transport.sent] == ["GET", "PUT"]


def test_http_append_default_is_rewrite():
    """Default append_mode should be 'rewrite'."""
    from pathlib_next.uri.schemes.http import HttpBackend

    assert HttpBackend(FakeTransport()).append_mode == "rewrite"


# --- httpdav-scandir-stat-hint-fabricates-size ---


def test_listing_without_sizes_gives_no_fabricated_stat(http_server, fixture_tree):
    # The stdlib `<ul>` index carries neither sizes nor dates.
    (fixture_tree / "big.bin").write_bytes(b"x" * 12345)
    root = UriPath(f"{http_server}/")
    child = next(c for c in root.iterdir() if c.name == "big.bin")
    first = child.stat()
    assert first.st_size == 12345
    assert first.st_mtime != 0
    sub = next(c for c in root.iterdir() if c.name == "sub")
    assert sub.is_dir()


def test_approximate_listing_size_is_not_a_stat_hint(site):
    site.index(
        "/d/",
        '<a href="f.txt">f.txt</a>   01-Jan-1970 00:00  1.2K\n',
        '<a href="g.txt">g.txt</a>   01-Jan-1970 00:00  7\n',
    )
    entries = dict(UriPath(site.url("/d/"))._scandir())
    assert entries["f.txt"].is_file() and not entries["f.txt"].size_known
    assert entries["g.txt"].st_size == 7 and entries["g.txt"].size_known


def test_listing_parser_marks_human_readable_sizes_approximate():
    from pathlib_next.uri.schemes.http import _DirectoryListingParser

    parser = _DirectoryListingParser()
    parser.feed(
        "<html><head><title>Index of /d/</title></head><body><pre>"
        '<a href="a.txt">a.txt</a>   11-Jul-2026 10:23  1.2K\n'
        '<a href="b.txt">b.txt</a>   11-Jul-2026 10:23  1,024\n'
        "</pre></body></html>"
    )
    parser.close()
    listing = {e.name: e for e in parser.listing}
    assert listing["a.txt"].size_exact is False
    assert listing["b.txt"].size_exact is True


def test_patch_append_offset_ignores_listing_hint(site):
    import requests

    site.index("/d/", '<a href="f.txt">f.txt</a>   01-Jan-1970 00:00  1228\n')
    site.serve("/d/f.txt", b"x" * 1000)
    root = UriPath(site.url("/d/")).with_session(
        requests.Session(), append_mode="patch"
    )
    child = next(root.iterdir())
    with child.open("ab") as f:
        f.write(b"hello!")
    patches = [r.headers["Content-Range"] for r in site.sent("PATCH")]
    assert patches == ["bytes 1000-1005/*"]

    # An empty append to an existing file sends no (invalid) range.
    site.seen.clear()
    with child.open("ab"):
        pass
    assert [r.method for r in site.seen] == ["HEAD"]
