"""`http:` and `gitlab:` listings that know a file's type and not its size
(a GitLab tree, an Apache index with sizes like "1.2K", the stdlib bulleted
index) cost one request per directory to walk, and none per file. A `stat()` of
a listed child still asks the server for its real size."""

import http.server
import json

import pytest

from http_fakes import spy_on_sends
from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes.github import RepoBackend
from pathlib_next.uri.schemes.gitlab import GitLabPath


@pytest.fixture
def sent(monkeypatch, http_transport_kind):
    """`[(method, path)]` of every request sent, by either transport."""
    return spy_on_sends(
        monkeypatch,
        lambda method, url, **kwargs: (
            method,
            "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/",
        ),
    )


def _gitlab(server, path=""):
    base_url, owner, repo = server
    backend = RepoBackend(api_base=f"{base_url}/api/v4")
    uri = f"gitlab://gitlab.com/{owner}/{repo}"
    return GitLabPath(f"{uri}/{path}" if path else uri, backend=backend)


def _kinds(sent):
    """How many tree listings and how many single-file requests were made."""
    trees = [r for r in sent if "/repository/tree" in r[1]]
    files = [r for r in sent if "/repository/files/" in r[1]]
    return len(trees), len(files)


def test_gitlab_walk_sends_one_request_per_directory_and_none_per_file(
    gitlab_api_server, sent
):
    root = _gitlab(gitlab_api_server)

    walked = [(top, files) for top, _dirs, files in root.walk()]

    names = sorted(name for _top, files in walked for name in files)
    # The six files of the fixture tree (git cannot hold an empty directory,
    # so the fake keeps a `.gitkeep` in it).
    assert names == [".gitkeep", ".hidden.txt", "a.txt", "b.py", "c.py", "d.py"]
    trees, files = _kinds(sent)
    assert files == 0
    assert trees == len(walked)


def test_gitlab_stat_of_a_listed_blob_is_exact_and_one_request(gitlab_api_server, sent):
    root = _gitlab(gitlab_api_server)
    child = next(p for p in root.iterdir() if p.name == "a.txt")
    sent.clear()

    stat = child.stat()

    assert (stat.st_size, stat.size_known) == (1, True)
    assert _kinds(sent) == (0, 1)


def test_gitlab_type_questions_about_listed_blobs_cost_nothing(gitlab_api_server, sent):
    root = _gitlab(gitlab_api_server)
    children = {c.name: c for c in root.iterdir()}
    others = {c.name: c for c in root.iterdir()}
    sent.clear()

    # A hint answers one question (it is single-use), so each is asked of its
    # own listed child.
    assert children["a.txt"].is_file() is True
    assert others["a.txt"].is_dir() is False
    assert children["sub"].is_dir() is True
    assert others["sub"].is_file() is False
    assert sent == []


def test_stdlib_index_walk_sends_one_get_per_directory_and_no_head(http_server, sent):
    root = UriPath(f"{http_server}/")

    walked = [(top, sorted(files)) for top, _dirs, files in root.walk()]

    methods = [m for m, _ in sent]
    assert methods.count("HEAD") == 0
    assert methods.count("GET") == len(walked)
    names = {name for _top, files in walked for name in files}
    assert names >= {"a.txt", "b.py", "c.py"}


def test_stdlib_index_stat_of_a_listed_file_is_exact(http_server, fixture_tree, sent):
    (fixture_tree / "big.bin").write_bytes(b"x" * 12345)
    root = UriPath(f"{http_server}/")
    child = next(c for c in root.iterdir() if c.name == "big.bin")
    sent.clear()

    stat = child.stat()

    assert (stat.st_size, stat.size_known) == (12345, True)
    assert [m for m, _ in sent] == ["HEAD"]


APACHE_ROOT = (
    "<html><head><title>Index of /d</title></head><body><h1>Index of /d</h1><pre>"
    '<a href="../">../</a>\n'
    '<a href="a.txt">a.txt</a>   11-Jul-2026 10:23  1.2K\n'
    '<a href="sub/">sub/</a>   11-Jul-2026 10:23    -\n'
    "</pre></body></html>"
)
APACHE_SUB = (
    "<html><head><title>Index of /d/sub</title></head><body><h1>Index of /d/sub</h1>"
    '<pre><a href="../">../</a>\n'
    '<a href="b.txt">b.txt</a>   11-Jul-2026 10:23  3.4K\n'
    "</pre></body></html>"
)


def _apache(serve_http, sizes):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _page(self, head):
            index = {"/d/": APACHE_ROOT, "/d/sub/": APACHE_SUB}.get(self.path)
            if index is not None:
                body = index.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            elif self.path in sizes:
                body = b"x" * sizes[self.path]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Last-Modified", "Sat, 11 Jul 2026 10:23:00 GMT")
            else:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def do_GET(self):
            self._page(False)

        def do_HEAD(self):
            self._page(True)

        def log_message(self, format, *args):
            pass

    return serve_http(Handler)


def test_apache_index_with_human_sizes_walks_without_a_head_per_file(serve_http, sent):
    with _apache(serve_http, {"/d/a.txt": 1228, "/d/sub/b.txt": 3481}) as base:
        root = UriPath(f"{base}/d/")
        entries = dict(root._scandir())

        walked = [(top, sorted(files)) for top, _dirs, files in root.walk()]

        assert entries["a.txt"].is_file() and not entries["a.txt"].size_known
        assert entries["sub"].is_dir()
        assert sorted(name for _top, files in walked for name in files) == [
            "a.txt",
            "b.txt",
        ]
        assert [m for m, _ in sent if m == "HEAD"] == []
        assert [m for m, _ in sent].count("GET") == 3  # _scandir + two walked


def test_apache_index_stat_of_a_listed_file_is_exact_not_the_rounded_column(
    serve_http, sent
):
    with _apache(serve_http, {"/d/a.txt": 1228}) as base:
        child = next(c for c in UriPath(f"{base}/d/").iterdir() if c.name == "a.txt")
        sent.clear()

        stat = child.stat()

        assert stat.st_size == 1228 and stat.size_known
        assert [m for m, _ in sent] == ["HEAD"]


def test_a_listing_with_exact_sizes_stays_a_full_hint(serve_http, sent):
    page = (
        "<html><head><title>Index of /d</title></head><body><pre>"
        '<a href="../">../</a>\n'
        '<a href="a.txt">a.txt</a>   11-Jul-2026 10:23  1228\n'
        "</pre></body></html>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with serve_http(Handler) as base:
        child = next(c for c in UriPath(f"{base}/d/").iterdir())
        sent.clear()

        stat = child.stat()

    assert (stat.st_size, stat.size_known) == (1228, True)
    assert sent == []
