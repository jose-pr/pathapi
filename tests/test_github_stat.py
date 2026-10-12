"""`github:` asks for a file's size without its body. The replies served here
have the headers the public API gave a `HEAD` of a file and of a directory
with the raw media type; whatever is not that shape is left to the `GET` of
the contents object. Every request goes to a loopback server."""

import errno
import http.server
import json

import pytest

pytestmark = pytest.mark.usefixtures("http_transport_kind")

from pathlib_next.uri import UriPath
from pathlib_next.uri.schemes._gitrepo import RepoBackend

FILE = b"x" * 70_000
API = {"X-GitHub-Media-Type": "github.v3; param=raw; format=json"}
RAW = {**API, "Content-Type": "application/vnd.github.raw+json; charset=utf-8"}
JSON = {**API, "Content-Type": "application/json; charset=utf-8"}

LISTING = json.dumps([{"name": "f.bin", "type": "file", "size": len(FILE)}]).encode()
OBJECT = json.dumps(
    {"name": "f.bin", "type": "file", "size": len(FILE), "encoding": "base64"}
).encode()
MISSING = json.dumps({"message": "Not Found"}).encode()


class _Api:
    """A loopback API. `head(path)` and `get(path)` return `(status, headers,
    body)`; a `HEAD` sends the headers only. `log` holds `(method, path)` and
    `sent` the request headers, in order."""

    def __init__(self):
        self.log = []
        self.sent = []
        self.head = self.get = lambda path: (404, JSON, MISSING)

    def gets(self):
        return [path for method, path in self.log if method == "GET"]

    def heads(self):
        return [path for method, path in self.log if method == "HEAD"]


@pytest.fixture
def api(serve_http):
    api = _Api()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _serve(self):
            api.log.append((self.command, self.path))
            api.sent.append(dict(self.headers.items()))
            route = api.head if self.command == "HEAD" else api.get
            status, headers, body = route(self.path)
            self.send_response(status)
            for key, value in {"Content-Length": str(len(body)), **headers}.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = do_HEAD = _serve

    with serve_http(Handler) as base:
        api.base = base
        yield api


def _path(api, name="dir/f.bin", query="", **backend):
    return UriPath(
        f"github://github.com/o/r/{name}{query}",
        backend=RepoBackend(api_base=api.base, **backend),
    )


def _live(api):
    """The file `dir/f.bin` and the directory `dir`, answered as the API does."""

    def head(path):
        name = path.partition("?")[0]
        if name == "/repos/o/r/contents/dir/f.bin":
            return 200, RAW, FILE
        if name == "/repos/o/r/contents/dir":
            return 200, JSON, LISTING
        return 404, JSON, MISSING

    def get(path):
        if path.partition("?")[0] == "/repos/o/r/contents/dir/f.bin":
            return 200, JSON, OBJECT
        return head(path)

    api.head, api.get = head, get


# --- the reply of the API ---------------------------------------------------------------------


def test_stat_of_a_file_is_one_request_without_a_body(api):
    _live(api)
    path = _path(api)
    stat = path.stat()
    assert (stat.st_size, stat.is_dir()) == (len(FILE), False)
    assert api.log == [("HEAD", "/repos/o/r/contents/dir/f.bin")]


@pytest.mark.parametrize("ask", ["exists", "is_file", "is_dir"])
def test_a_question_about_a_file_downloads_nothing(api, ask):
    _live(api)
    assert getattr(_path(api), ask)() is (ask != "is_dir")
    assert api.gets() == []


def test_the_request_asks_for_the_raw_type_unencoded_at_the_ref_with_the_token(api):
    _live(api)
    _path(api, query="?ref=v1", token="t0ken").stat()
    assert api.log == [("HEAD", "/repos/o/r/contents/dir/f.bin?ref=v1")]
    sent = {key.lower(): value for key, value in api.sent[0].items()}
    assert sent["accept"] == "application/vnd.github.raw+json"
    assert sent["accept-encoding"] == "identity"
    assert sent["authorization"] == "Bearer t0ken"


def test_an_empty_file_has_size_zero_and_is_a_file(api):
    api.head = lambda path: (200, RAW, b"")
    stat = _path(api).stat()
    assert (stat.st_size, stat.is_dir()) == (0, False)
    assert api.gets() == []


def test_stat_of_a_directory_reads_the_listing(api):
    _live(api)
    assert _path(api, "dir").stat().is_dir()
    assert api.log == [
        ("HEAD", "/repos/o/r/contents/dir"),
        ("GET", "/repos/o/r/contents/dir"),
    ]


def test_the_root_of_a_repository_is_asked_for_its_listing_only(api):
    api.get = lambda path: (200, JSON, LISTING)
    assert _path(api, "").stat().is_dir()
    assert api.log == [("GET", "/repos/o/r/contents")]


def test_a_missing_path_is_settled_by_the_first_request(api):
    _live(api)
    path = _path(api, "dir/none.bin")
    with pytest.raises(FileNotFoundError):
        path.stat()
    assert path.exists() is False
    assert api.gets() == [] and len(api.heads()) == 2


def test_a_rate_limited_reply_is_eagain(api):
    api.head = lambda path: (
        403,
        {**JSON, "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"},
        b"{}",
    )
    with pytest.raises(OSError) as raised:
        _path(api).stat()
    assert raised.value.errno == errno.EAGAIN
    assert api.gets() == []


# --- a reply that does not settle it: the GET of the contents object decides -----------------


def _falls_back(api, head):
    api.head = lambda path: head
    api.get = lambda path: (200, JSON, OBJECT)
    stat = _path(api).stat()
    assert (stat.st_size, stat.is_dir()) == (len(FILE), False)
    assert [method for method, _path_ in api.log] == ["HEAD", "GET"]


@pytest.mark.parametrize("status", [405, 501])
def test_a_server_without_head_is_asked_with_get(api, status):
    _falls_back(api, (status, {}, b""))


@pytest.mark.parametrize(
    "head",
    [
        (200, {"Content-Type": "application/vnd.github.raw+json"}, b"<p>12345</p>"),
        (200, {"Content-Type": "text/html"}, b"<html>login</html>"),
        (404, {"Content-Type": "text/html"}, b"<html>no such page</html>"),
        (403, {"Content-Type": "text/html"}, b"<html>HEAD is not allowed</html>"),
    ],
    ids=["raw-type-200", "html-200", "html-404", "html-403"],
)
def test_a_reply_that_is_not_the_api_s_own_is_not_believed(api, head):
    _falls_back(api, head)


@pytest.mark.parametrize(
    "headers",
    [
        {**RAW, "Content-Encoding": "gzip"},
        {**RAW, "Content-Length": "many"},
        {**RAW, "Content-Length": "-1"},
        {**API, "Content-Type": "application/octet-stream"},
        {**API},
        JSON,
    ],
    ids=["gzip", "length-text", "length-negative", "octet-stream", "no-type", "json"],
)
def test_a_reply_that_states_no_file_size_is_followed_by_the_get(api, headers):
    _falls_back(api, (200, headers, b"12345"))


def test_what_the_get_answers_is_what_stat_reports_after_an_unsettled_head(api):
    api.head = lambda path: (501, {}, b"")
    api.get = lambda path: (404, JSON, MISSING)
    path = _path(api)
    with pytest.raises(FileNotFoundError):
        path.stat()
    api.get = lambda path: (200, {"Content-Type": "text/html"}, b"<html>login</html>")
    with pytest.raises(OSError) as raised:
        path.stat()
    assert raised.value.errno == errno.EIO
